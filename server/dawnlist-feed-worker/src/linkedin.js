/**
 * LinkedIn Job Library (the Ad Library's jobs sub-API), as a Worker adapter.
 *
 * Free, no per-row billing, and it returns only PAID/sponsored postings. It is
 * searched ALONGSIDE TheirStack on every search and the two result sets are
 * combined; the app then keeps the richer copy of any role both returned.
 *
 * WHAT THE API IS, measured 2026-09-30 against the live service:
 *   - one `keyword` string, whose WORDS ARE ANDed ("Product Manager Product
 *     Strategy" matched 357k postings, fewer than either title alone), so each
 *     title is its own request;
 *   - it matches the keyword against whole DESCRIPTIONS ("Hotel Manager"
 *     returned a supermarket assistant) and offers no city, title or date
 *     filter, and quotes are ignored — so this adapter filters what comes back;
 *   - `countries` must be `List(urn%3Ali%3Acountry%3Agb)` with the colons
 *     percent-encoded; the older `(value:List(...))` form and `sortBy.*` are
 *     rejected with a 400;
 *   - a page can return a row short (23 of 24) with more behind it: the `next`
 *     link is the end, not the count;
 *   - it tolerates a dozen pages in flight at once.
 *
 * SUBREQUEST BUDGET. A Worker request may make 50 subrequests on the free plan.
 * A search spends at most the page budget here, one call to TheirStack (two
 * with paging) and up to a few place lookups, so it stays under it.
 *
 * THE DAILY QUOTA IS THE REAL LIMIT. LinkedIn throttles this resource per
 * application and per member per UTC day ("Resource level throttle
 * APPLICATION_AND_MEMBER DAY limit ... is reached", HTTP 429), the number is
 * not published (Developer Portal > the app > Analytics shows it), and it is not
 * customisable. Ordinary testing exhausted it on 2026-09-30. There is ONE token
 * for the whole Worker, so every customer's searches draw on one pool. Hence: a
 * small default page budget, a long cache, a 429 that degrades instead of
 * failing, and a breaker (see index.js) that stops asking until the day rolls.
 */
import { locationMatches } from './places.js';

export const PAGE_SIZE = 24;
/**
 * Raw pages scanned per search, shared across its titles. SMALL ON PURPOSE: it
 * is a slice of one shared daily quota, and the API is relevance-ranked over
 * descriptions, so a bigger scan buys a few more rows at the cost of everyone's
 * calls. `env.LINKEDIN_PAGE_BUDGET` overrides it without a deploy of new logic.
 */
export const PAGE_BUDGET = 8;
export const MAX_PAGE_BUDGET = 48;
/** How long a LinkedIn answer is kept: sponsored postings change slowly and the
 *  quota is daily, so a day of reuse costs little and saves a lot. */
export const CACHE_TTL_SECONDS = 24 * 60 * 60;
/** Pages in flight at once. */
export const PARALLEL = 6;
export const MAX_KEYWORDS = 3;
const API = 'https://api.linkedin.com/rest/jobLibrary';
export const PROVIDER = 'linkedin-joblibrary';
/** One page may take this long. LinkedIn is searched ALONGSIDE TheirStack, so a
 *  hung request here would otherwise hold up every search that also has a
 *  perfectly good TheirStack answer; it becomes a degraded provider instead. */
export const PAGE_TIMEOUT_MS = 10000;

// --- request ---------------------------------------------------------------

export function keywordsFor(q) {
  const words = (q.titles?.length ? q.titles : q.descriptionKeywords) || [];
  return words.filter((w) => w && String(w).trim()).slice(0, MAX_KEYWORDS);
}

export function linkedinParams(q, start, count, keyword) {
  const params = { q: 'criteria', start: String(start), count: String(count) };
  if (keyword) params.keyword = keyword;
  if (q.companies?.length) params.organization = q.companies[0];
  if (q.countries?.length) {
    const urns = q.countries.map((c) => `urn:li:country:${String(c).toLowerCase() === 'uk' ? 'gb' : String(c).toLowerCase()}`);
    params.countries = `List(${urns.join(',')})`;
  }
  return params;
}

/** Each value encoded on its own, keeping Rest.li's List(a,b) structure. */
export function encodeQuery(params) {
  return Object.entries(params)
    .map(([k, v]) => `${k}=${encodeURIComponent(v).replace(/%2C/gi, ',')}`)
    .join('&');
}

// --- filters: what the API cannot do for us ------------------------------------

const SUFFIXES = ['ement', 'ers', 'er', 'ing', 'ment', 'ant', 'ancy', 's'];

/** Crude enough to match "Manager" to "Management" and nothing looser. */
export function stem(word) {
  const w = word.toLowerCase();
  for (const suf of SUFFIXES) {
    if (w.endsWith(suf) && w.length - suf.length >= 4) return w.slice(0, -suf.length);
  }
  return w;
}

function wordSet(text) {
  return new Set((String(text || '').match(/[A-Za-z0-9]+/g) || []).map(stem));
}

/** Every word of at least one wanted title is in the posting's title, in any order. */
export function titleMatches(jobTitle, wanted) {
  const have = wordSet(jobTitle);
  return (wanted || []).some((t) => {
    const need = wordSet(t);
    return need.size > 0 && [...need].every((w) => have.has(w));
  });
}

export function decodeEntities(text) {
  return String(text || '')
    .replace(/&nbsp;/gi, ' ').replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&amp;/gi, '&');
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Whole-word, case-insensitive: "asset" must not match "inasset". */
export function mentions(text, phrases) {
  return (phrases || []).some((p) => p && p.trim()
    && new RegExp(`\\b${escapeRe(p.trim())}\\b`, 'i').test(text));
}

/**
 * The three search types as the TheirStack feed reads them.
 *   title        every word of a wanted title is in the TITLE; if description
 *                keywords are also named, one of them must be in the text
 *   description  a wanted title or keyword appears in the description
 *   both         either of the above
 */
export function matchesSearchType(job, q) {
  const mode = q.searchType || 'title';
  const text = decodeEntities(job.description_text);
  const titles = q.titles || [];
  const keywords = q.descriptionKeywords || [];
  const nothing = !titles.length && !keywords.length;
  if (mode === 'description') return nothing || mentions(text, [...titles, ...keywords]);
  if (mode === 'both') {
    return nothing || (titles.length > 0 && titleMatches(job.title, titles))
      || mentions(text, keywords);
  }
  const titleOk = !titles.length || titleMatches(job.title, titles);
  return titleOk && (!keywords.length || mentions(text, keywords));
}

export function keep(job, q, today) {
  if (!matchesSearchType(job, q)) return false;
  if (q.cities?.length) {
    const verdict = locationMatches(job.locations, q.cities);
    if (verdict === false) return false;
    // Located only as "United Kingdom"/"England": unknown is not evidence of
    // elsewhere. Kept, and marked so the assessment knows the place is open.
    if (verdict === null) job.raw_criteria.location_unresolved = true;
  }
  if (q.postedWithinDays && job.posted_at) {
    const cutoff = new Date(today.getTime() - q.postedWithinDays * 86400000);
    if (new Date(job.posted_at) < cutoff) return false;
  }
  for (const term of q.excludeTitleTerms || []) {
    if (term && term.trim() && new RegExp(`\\b${escapeRe(term.trim())}\\b`, 'i').test(job.title)) {
      return false;
    }
  }
  return true;
}

// --- the adapter ---------------------------------------------------------------

/**
 * @param {{HttpError: Function}} deps  HttpError comes in rather than being
 *        imported, because index.js imports this module.
 */
export function makeLinkedInAdapter({ HttpError }) {
  return {
    /** No per-row cost, so its rows never draw on the licence's allowance. */
    billed: false,
    /** It filters by the city NAME, so it does not need the feed's place ids. */
    needsPlaces: false,
    /** A daily quota is the limit, so its answers live longer than TheirStack's. */
    cacheTtl: CACHE_TTL_SECONDS,
    /** A 429 from this provider trips the breaker in index.js until UTC midnight. */
    quotaLimited: true,

    async search(env, q, _headroom, deps = {}) {
      const doFetch = deps.fetch || fetch;
      const token = env.LINKEDIN_ACCESS_TOKEN;
      if (!token) {
        throw new HttpError(503, 'provider_not_configured',
          'LinkedIn access token is not set');
      }
      const keywords = keywordsFor(q);
      if (!keywords.length && !q.companies?.length) {
        // The API answers such a search with a bare 500. Nothing to ask for.
        return { jobs: [], total: 0, scanned: 0, exhausted: true, pages: 0 };
      }

      const wanted = Math.min(q.maxResults ?? q.limit ?? 24, 240);
      const today = deps.today || new Date();
      const countries = (q.countries || []).map((c) => String(c).toUpperCase());
      const budget = Math.min(MAX_PAGE_BUDGET,
        Math.max(1, Number(env.LINKEDIN_PAGE_BUDGET) || PAGE_BUDGET));
      const perKeyword = Math.max(1, Math.floor(budget / Math.max(1, keywords.length)));
      let throttled = false;
      const jobs = [];
      const seen = new Set();
      let total = 0;
      let scanned = 0;
      let pages = 0;
      let exhausted = true;
      let partial = null;

      const fetchPage = async (keyword, start) => {
        const url = `${API}?${encodeQuery(linkedinParams(q, start, PAGE_SIZE, keyword))}`;
        const res = await doFetch(url, {
          signal: AbortSignal.timeout(PAGE_TIMEOUT_MS),
          headers: {
            Authorization: `Bearer ${token}`,
            'X-RestLi-Protocol-Version': '2.0.0',
            'Linkedin-Version': '202607',
          },
        });
        if (res.status === 401) {
          throw new HttpError(502, 'token_expired', 'LinkedIn token expired or invalid');
        }
        if (res.status === 429) {
          throw new HttpError(429, 'rate_limited',
            'LinkedIn daily quota for this resource is reached');
        }
        if (!res.ok) {
          const body = await res.text().catch(() => '');
          throw new HttpError(502, 'provider_error',
            `linkedin returned ${res.status}: ${body.slice(0, 200)}`);
        }
        return res.json();
      };

      outer:
      for (const keyword of (keywords.length ? keywords : [null])) {
        let done = false;
        let index = 0;
        while (!done && index < perKeyword && jobs.length < wanted) {
          const n = Math.min(PARALLEL, perKeyword - index);
          const settled = await Promise.all(
            Array.from({ length: n }, (_, i) =>
              fetchPage(keyword, (index + i) * PAGE_SIZE).catch((err) => err)));
          for (const [i, page] of settled.entries()) {
            if (page instanceof Error) {
              // An expired token is the caller's to hear about; anything else
              // after some rows were read keeps those rows and says so.
              if (page.code === 'token_expired' || !jobs.length) throw page;
              partial = page.message;
              throttled = page.code === 'rate_limited';
              exhausted = false;
              break outer;
            }
            const paging = page.paging || {};
            if (index === 0 && i === 0 && Number.isInteger(paging.total)) total += paging.total;
            const elements = page.elements || [];
            pages += 1;
            for (const el of elements) {
              if (el.isRestricted) continue;
              const job = normaliseLinkedIn(el);
              if (!job || seen.has(job.provider_job_id)) continue;
              scanned += 1;
              if (countries.length) job.raw_criteria.country_codes = countries;
              if (keep(job, q, today)) {
                seen.add(job.provider_job_id);
                jobs.push(job);
                if (jobs.length >= wanted) break;
              }
            }
            // Enough kept: the rest of this batch is already in flight, but its
            // rows are not needed, and taking them overshot the limit.
            if (jobs.length >= wanted) { done = true; break; }
            // A short page is NOT the end; the absence of a `next` link is.
            if (!elements.length || !paging.links?.length) { done = true; break; }
          }
          index += n;
        }
        // The budget, not the data, ended this keyword.
        if (!done && jobs.length < wanted) exhausted = false;
        if (jobs.length >= wanted) break;
      }
      return { jobs, total, scanned, exhausted, pages,
               ...(partial ? { partial } : {}), ...(throttled ? { throttled } : {}) };
    },
  };
}

// --- response -> the common job shape -----------------------------------------

export function normaliseLinkedIn(el) {
  const d = el.jobDetails;
  if (!d) return null;
  const title = d.jobTitle || '';
  if (title.startsWith('This information is not available')) return null;
  const location = d.jobLocation || '';
  const locations = (location && !location.startsWith('This information'))
    ? [location] : [];

  let description = d.jobDescription || '';
  if (description.startsWith('This information is not available')) description = '';

  const postedMs = d.jobListTimeInMilliseconds;
  let posted_at = null;
  if (postedMs) {
    posted_at = new Date(postedMs).toISOString().slice(0, 10);
  }

  const url = el.jobPostingUrl || '';
  let job_id = '';
  if (url) {
    const parts = url.replace(/\/+$/, '').split('/');
    const last = parts[parts.length - 1];
    if (/^\d+$/.test(last)) job_id = last;
  }
  if (!job_id) job_id = url;

  const raw = {};
  const targeting = d.jobTargeting || [];
  for (const t of targeting) {
    if (t.facetName === 'Location' && t.includedSegments?.length) {
      raw.target_locations = t.includedSegments;
    }
  }
  const stats = d.jobStatistics || {};
  if (stats.totalImpressions) raw.impressions = stats.totalImpressions;
  if (d.jobApplyMethod) raw.apply_method = d.jobApplyMethod;
  if (d.payerName) raw.payer = d.payerName;
  if (d.jobBenefits?.length) raw.benefits = d.jobBenefits;

  const sal = d.jobSalaryRange;
  let salary = null;
  if (sal) {
    const lo = sal.minBaseSalary;
    const hi = sal.maxBaseSalary;
    const cur = sal.currencyCode || '';
    let period = sal.payPeriod || '';
    period = period.replace('CompensationPeriod_', '');
    const suffix = (period && period !== 'YEARLY')
      ? ` (${period.toLowerCase()})` : '';
    if (lo && hi) salary = `${cur} ${lo}–${hi}${suffix}`.trim();
    else if (lo) salary = `${cur} ${lo}+${suffix}`.trim();
    else if (hi) salary = `Up to ${cur} ${hi}${suffix}`.trim();
  }

  return {
    provider: 'linkedin-joblibrary',
    provider_job_id: job_id,
    title,
    company: d.organizationName || '',
    locations,
    description_text: description,
    posted_at,
    salary,
    url,
    raw_criteria: raw,
  };
}
