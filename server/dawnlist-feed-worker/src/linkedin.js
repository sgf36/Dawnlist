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
 * for the whole Worker, so every customer's searches draw on one pool. LinkedIn's
 * terms also forbid getting round it with more tokens or applications, or by
 * making users bring their own credentials (API Terms 2.2, 3.1(20)).
 *
 * SO THE COST MUST SCALE WITH WHAT IS NEW, NOT WITH THE WINDOW. The reference
 * documents the default sort as LISTED_TIME descending, and 480 rows in each of
 * four real searches confirmed it (no out-of-order pair). So a scan reads
 * newest-first and STOPS at a cutoff: the last complete scan's newest posting
 * less an overlap, or, the first time, a few days back. Measured on ten real
 * searches, that is about 26 pages per day of new postings against about 80 for
 * a fixed-budget scan.
 *
 * TWO THINGS THE CURSOR MUST RESPECT, both visible in the data:
 *   - The API LAGS: the newest posting was about two days old when fetched. A
 *     cursor taken from the wall clock would skip anything that arrives late,
 *     so the cursor is the newest posting time actually READ (`newestMs`), and
 *     the next scan starts an OVERLAP before it.
 *   - A scan that ends on a budget, an error or the row limit has NOT reached
 *     its cutoff. It reports `complete: false` and its cursor must not move, or
 *     the unread postings between are lost for good.
 * If a response ever arrives out of order the cutoff is not trusted for that
 * keyword and the scan falls back to its page budget.
 */
import { locationMatches } from './places.js';

export const PAGE_SIZE = 24;
/**
 * The most pages ONE search may spend, shared across its titles. It is a
 * ceiling, not a target: a scan stops as soon as it reaches its cutoff, which in
 * steady state is one to a few pages a title. The licence's daily allowance and
 * the Worker-wide ceiling (index.js) bound the total. `env.LINKEDIN_PAGE_BUDGET`
 * overrides it.
 */
export const PAGE_BUDGET = 16;
export const MAX_PAGE_BUDGET = 48;
/** With no cursor yet, read back only this far FROM THE NEWEST POSTING READ,
 *  whatever the search's window: a backfill of a 14-day window is ~280 pages on
 *  a broad title. */
export const FIRST_RUN_DAYS = 3;
/** A scan starts this far BEFORE the cursor. The API lags and can deliver late,
 *  and a run that crashed after the cursor moved must not lose its postings. */
export const OVERLAP_MS = 24 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
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
    /**
     * Not kept in the shared cache. A scan starts at a per-licence cursor, so an
     * answer is only right for the licence that asked, and LinkedIn's terms do
     * not allow caching its content beyond what they expressly permit (API
     * Terms 4.1), so nothing here is stored.
     */
    cacheable: false,
    /** Spends pages against a daily allowance, reserved by index.js. */
    pagedAllowance: true,
    /** A 429 from this provider trips the breaker in index.js until UTC midnight. */
    quotaLimited: true,

    /**
     * @param {object} deps
     *   fetch, today   injected by tests
     *   pages          pages this search may spend (reserved by the caller);
     *                  without it the environment budget is used
     *   cursors        {keyword: newestMs} from the last COMPLETE scan of each
     *                  keyword. Passing an object (even empty) makes the scan
     *                  incremental; leaving it out scans back to the window.
     */
    async search(env, q, _headroom, deps = {}) {
      let pagesUsed = 0;
      try {
        return await scan(env, q, deps, (n) => { pagesUsed += n; }, () => pagesUsed);
      } catch (err) {
        // The caller refunds what a failed scan did not spend.
        err.pagesUsed = pagesUsed;
        throw err;
      }
    },
  };

  async function scan(env, q, deps, spend, spent) {
    const doFetch = deps.fetch || fetch;
    const token = env.LINKEDIN_ACCESS_TOKEN;
    if (!token) {
      throw new HttpError(503, 'provider_not_configured', 'LinkedIn access token is not set');
    }
    const keywords = keywordsFor(q);
    if (!keywords.length && !q.companies?.length) {
      // The API answers such a search with a bare 500. Nothing to ask for.
      return { jobs: [], total: 0, scanned: 0, exhausted: true, pages: 0, keywords: [] };
    }

    const wanted = Math.min(q.maxResults ?? q.limit ?? 24, 240);
    const now = deps.today || new Date();
    const countries = (q.countries || []).map((c) => String(c).toUpperCase());
    const incremental = deps.cursors !== undefined;
    const cursors = deps.cursors || {};
    const windowCutoff = q.postedWithinDays ? now.getTime() - q.postedWithinDays * DAY_MS : 0;
    const budget = deps.pages ?? Math.min(MAX_PAGE_BUDGET,
      Math.max(1, Number(env.LINKEDIN_PAGE_BUDGET) || PAGE_BUDGET));

    const jobs = [];
    const seen = new Set();
    const states = [];
    let total = 0;
    let scanned = 0;
    let partial = null;
    let throttled = false;

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
        throw new HttpError(429, 'rate_limited', 'LinkedIn daily quota for this resource is reached');
      }
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new HttpError(502, 'provider_error', `linkedin returned ${res.status}: ${body.slice(0, 200)}`);
      }
      return res.json();
    };

    const listedMs = (el) => Number(el.jobDetails?.jobListTimeInMilliseconds) || 0;

    outer:
    for (const [index, keyword] of (keywords.length ? keywords : [null]).entries()) {
      // Unused pages roll over to the keywords after this one.
      const allowance = Math.floor((budget - spent()) / Math.max(1, keywords.length - index));
      const cursor = cursors[keyword];
      // With a cursor the cutoff is data-relative already. With none, it is
      // FIRST_RUN_DAYS before the newest posting READ, set on the first page:
      // measured from the clock it would be wrong, because the API lags (the
      // newest posting was about two days old), so "three days back" covered
      // one day of postings.
      const firstRun = incremental && !cursor;
      let cutoff = Math.max(windowCutoff, cursor ? cursor - OVERLAP_MS : 0);

      const state = { keyword, complete: false, newestMs: 0, pages: 0, unsorted: false };
      let page = 0;
      let reached = false;
      let ended = false;
      let oldestSoFar = Infinity;
      let spanPerPage = 0;       // observed time covered by one page

      while (state.pages < allowance && !reached && !ended && jobs.length < wanted) {
        // After the first page, size the batch from how densely postings fall:
        // fetching the pages still needed, and no more, because every page past
        // the cutoff is quota spent on nothing.
        let n = 1;
        if (page > 0) {
          const left = allowance - state.pages;
          let estimate = PARALLEL;
          if (cutoff && spanPerPage > 0 && Number.isFinite(oldestSoFar)) {
            estimate = Math.max(1, Math.ceil((oldestSoFar - cutoff) / spanPerPage));
          }
          n = Math.max(1, Math.min(estimate, PARALLEL, left));
        }
        const settled = await Promise.all(
          Array.from({ length: n }, (_, i) => fetchPage(keyword, (page + i) * PAGE_SIZE).catch((err) => err)));
        spend(n);
        state.pages += n;

        for (const [i, result] of settled.entries()) {
          if (result instanceof Error) {
            // An expired token is the caller's to hear about; anything else
            // after some rows were read keeps those rows and says so.
            if (result.code === 'token_expired' || !jobs.length) throw result;
            partial = result.message;
            throttled = result.code === 'rate_limited';
            states.push(state);
            break outer;
          }
          const paging = result.paging || {};
          if (page === 0 && i === 0 && Number.isInteger(paging.total)) total += paging.total;
          const elements = result.elements || [];

          // Newest-first is what the reference says and the data showed. If a
          // page starts newer than the previous one ended, it is not: stop
          // trusting the cutoff for this keyword and let the budget bound it.
          const firstMs = elements.length ? listedMs(elements[0]) : 0;
          if (firstRun && page === 0 && i === 0 && firstMs) {
            cutoff = Math.max(windowCutoff, firstMs - FIRST_RUN_DAYS * DAY_MS);
          }
          if (firstMs && oldestSoFar !== Infinity && firstMs > oldestSoFar + 60 * 1000) {
            state.unsorted = true;
          }

          for (const el of elements) {
            if (el.isRestricted) continue;
            const ms = listedMs(el);
            if (ms) {
              state.newestMs = Math.max(state.newestMs, ms);
              if (!state.unsorted && cutoff && ms < cutoff) { reached = true; continue; }
              oldestSoFar = Math.min(oldestSoFar, ms);
            }
            const job = normaliseLinkedIn(el);
            if (!job || seen.has(job.provider_job_id)) continue;
            scanned += 1;
            if (countries.length) job.raw_criteria.country_codes = countries;
            if (keep(job, q, now)) {
              seen.add(job.provider_job_id);
              jobs.push(job);
              if (jobs.length >= wanted) break;
            }
          }
          if (Number.isFinite(oldestSoFar) && state.newestMs) {
            spanPerPage = Math.max(spanPerPage, (state.newestMs - oldestSoFar) / (page + i + 1));
          }
          // A short page is NOT the end; the absence of a `next` link is.
          if (!elements.length || !paging.links?.length) ended = true;
          if (reached || ended || jobs.length >= wanted) break;
        }
        page += n;
      }
      state.complete = (reached && !state.unsorted) || ended;
      states.push(state);
      if (jobs.length >= wanted) break;
    }

    return {
      jobs, total, scanned,
      exhausted: states.length > 0 && states.every((s) => s.complete) && !partial,
      pages: spent(),
      keywords: states.map((s) => ({ keyword: s.keyword, complete: s.complete, newestMs: s.newestMs, pages: s.pages, unsorted: s.unsorted })),
      ...(partial ? { partial } : {}),
      ...(throttled ? { throttled } : {}),
    };
  }
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
