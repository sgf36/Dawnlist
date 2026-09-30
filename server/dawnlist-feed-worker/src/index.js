/**
 * Dawnlist feed proxy.
 *
 * Sits between the app and both upstreams (the job-data provider and
 * Anthropic) so that:
 *   - the managed-tier user never sees an API key;
 *   - usage is metered per licence in D1 and fair-use caps are enforced;
 *   - query results are cached ACROSS users, so two people searching
 *     "hotel general manager, Dubai" share one upstream call — when both are
 *     served by the same Cloudflare data centre. The Cache API is local to
 *     each data centre, not global, so the saving grows with how concentrated
 *     users are, not simply with how many there are;
 *   - the provider is a config value, swappable with no app release.
 *
 * That last point is the structural answer to the data-source fragility that
 * ruled out scraping: the app speaks only this API's shape, so adding a
 * provider — or moving to a self-hosted dataset index — is a change here.
 *
 * Pattern reuse: paddle-license-webhook-worker, easypost-mobile-proxy, the
 * wren comp-codes Worker. READ WEBHOOK-RUNBOOK.md before touching anything
 * Paddle-related; that Worker broke four times and the runbook says why.
 *
 * WHAT THIS WORKER DELIBERATELY DOES NOT DO: run inference.
 *
 * Dawnlist is bring-your-own-key. Job descriptions, the fit brief and the
 * background factsheet are the user's employment history, and proxying them
 * through here would make Spencer a processor of every buyer's career record —
 * with the retention, breach-notification and international-transfer duties
 * that follow. The app calls Anthropic directly on the user's own key, so none
 * of that data touches this service at all. An inference proxy was built and
 * removed on 2026-09-06; do not reintroduce it as a convenience.
 *
 * What DOES pass through here is a job search (query terms) and licence
 * metering. That is the whole of it.
 *
 * Logging rule, and it is load-bearing for the store privacy labels: log
 * COUNTS, never CONTENT. No job descriptions, no CVs, no queries with personal
 * text are ever written to a log line.
 */

import { makeLinkedInAdapter } from './linkedin.js';
import { newLicenceKey } from './paddle.js';
import { handlePaddleWebhook } from './paddle.js';
import { handleAdmin, handleRedeem } from './codes.js';
import { handleApple } from './apple.js';
import { handleMicrosoft, handleTicket } from './microsoft.js';
import { PLANS, FALLBACK_PLAN, hasExpired, sellablePlans } from './plans.js';
import { parseJsonObject } from './body.js';
import { purgeExpired } from './retention.js';

const SEARCH_TTL_SECONDS = 6 * 60 * 60;   // 6h on search results
// Place ids do not change, and the catalogue's cost per lookup is not
// documented, so each place name is looked up once a month rather than once
// per search.
const PLACE_TTL_SECONDS = 30 * 24 * 60 * 60;
const DETAIL_TTL_SECONDS = 7 * 24 * 60 * 60;

/**
 * CAP UNIT: postings FETCHED, never postings assessed.
 *
 * The billable event is the fetch — 1 credit = 1 job RETURNED — and it happens
 * before the free local screen runs. Capping the downstream stage (assessment)
 * would govern roughly a tenth of variable cost and leave the rest open: a
 * licence could exhaust an unbounded amount of credit without ever tripping it.
 * Measured 2026-09-07 against the dataset sample: ~513 fetched/day yields
 * ~68-93 reaching assessment, so the spec's "target 30-60 reaching assessment"
 * dial is a CONSEQUENCE of this cap, not the cap itself. Do not re-express this
 * limit in assessed postings.
 *
 * The number lives per-licence in D1 (`licences.max_postings_per_day`) so the
 * cap can move with the price and the credit tier without a deploy. These are
 * only the fallbacks.
 */
const DEFAULTS = {
  maxSavedQueries: 10,
  maxRefreshesPerDay: 3,
  // An anti-abuse ceiling, not a product tier. A comprehensive UK user measures
  // ~513 fetched/day; this sits above that so the cap does not quietly become
  // the thing that decides coverage, while still stopping one runaway query
  // from spending an unbounded amount of the credit balance.
  maxPostingsPerDay: 700,
};

/** Hard ceiling on a single upstream page, independent of the licence cap. */
const MAX_PAGE = 500;

class HttpError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });

/**
 * Paginated fetch from TheirStack's /v1/jobs/search. Returns at most `asked`
 * normalised jobs and the total_results from the first page.
 */
async function fetchPaged(base, asked, env) {
  const jobs = [];
  let total = null;
  while (jobs.length < asked) {
    const limit = Math.min(MAX_PAGE, asked - jobs.length);
    const res = await fetch('https://api.theirstack.com/v1/jobs/search', {
      method: 'POST',
      headers: {
        authorization: `Bearer ${env.THEIRSTACK_API_KEY}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        ...base,
        limit,
        offset: jobs.length,
        include_total_results: jobs.length === 0,
      }),
    });
    if (!res.ok) {
      const errBody = await res.text().catch(() => '');
      const snippet = errBody.slice(0, 200);
      console.error('theirstack error', { status: res.status, body: snippet });
      throw new HttpError(502, 'provider_error',
        `theirstack returned ${res.status}: ${snippet}`);
    }
    const payload = await res.json();
    const rows = payload.data || [];
    if (jobs.length === 0) {
      total = payload.metadata?.total_results ?? null;
    }
    jobs.push(...rows.map(normaliseTheirStack));
    if (rows.length < limit) break;
    if (typeof total === 'number' && jobs.length >= total) break;
  }
  return { jobs, total };
}

// ---------------------------------------------------------------------------
// The search body — refused before anything is reserved or fetched
// ---------------------------------------------------------------------------

/**
 * The most postings one search may ask for. Paging exists so that a request
 * above one page is honoured rather than cut to a hundred; ten pages bounds how
 * many provider calls one invocation can make. The daily cap, not this, decides
 * what is billed.
 */
const MAX_RESULTS_PER_SEARCH = 1000;

/**
 * A wider window only widens what is fetched and billed; a posting a year old
 * is not one anybody is still hiring for.
 */
const MAX_POSTED_WITHIN_DAYS = 365;

/**
 * Bounds on each list. Most sit far above anything the app builds from what a
 * person states, so they refuse only a malformed or hostile body. Three are
 * set by something specific:
 */
const SEARCH_LISTS = {
  titles:            { items: 50,   chars: 200 },
  // ISO 3166-1 has 249 codes, so a search of every country still fits.
  countries:         { items: 250,  chars: 8 },
  companies:         { items: 200,  chars: 200 },
  // A name the place cache has not seen costs a catalogue lookup for each
  // listed country, so this list multiplies provider calls.
  cities:            { items: 20,   chars: 100 },
  excludeTitleTerms: { items: 100,  chars: 200 },
  excludeCompanies:  { items: 500,  chars: 200 },
  // Whole-word matches against the job description. Same bound as titles,
  // because both are search terms the user typed.
  descriptionKeywords: { items: 50, chars: 200 },
  // The app sends its 1,000 most recently held ids (RECENT_HELD_IDS). Twice
  // that leaves room, and stops an unbounded list being forwarded to the feed
  // with every page.
  excludeJobIds:     { items: 2000, chars: 100 },
};

const invalidField = (field, rule) =>
  new HttpError(400, 'invalid_field', `${field} ${rule}`);

function checkWholeNumber(body, field, min, max) {
  const value = body[field];
  if (value === undefined || value === null) return;
  if (!Number.isInteger(value) || value < min || value > max) {
    throw invalidField(field, `must be a whole number from ${min} to ${max}`);
  }
}

/** The search body, or a 400 naming the field — never a body the code cannot use. */
async function readSearch(request) {
  const parsed = await parseJsonObject(request);
  if (!parsed.ok) throw new HttpError(400, parsed.error, parsed.message);
  const body = parsed.body;

  checkWholeNumber(body, 'maxResults', 1, MAX_RESULTS_PER_SEARCH);
  checkWholeNumber(body, 'limit', 1, MAX_RESULTS_PER_SEARCH);
  checkWholeNumber(body, 'postedWithinDays', 1, MAX_POSTED_WITHIN_DAYS);

  // The app's feed_job_ids() returns integers; normalise before validation.
  if (Array.isArray(body.excludeJobIds)) {
    body.excludeJobIds = body.excludeJobIds.map((v) =>
      typeof v === 'number' && Number.isInteger(v) ? String(v) : v);
  }

  for (const [field, cap] of Object.entries(SEARCH_LISTS)) {
    const list = body[field];
    if (list === undefined || list === null) continue;
    const fits = Array.isArray(list) && list.length <= cap.items
      && list.every((s) => typeof s === 'string' && s.trim() !== '' && s.length <= cap.chars);
    if (!fits) {
      throw invalidField(field, `must be a list of up to ${cap.items} non-blank strings `
        + `of at most ${cap.chars} characters`);
    }
  }

  const st = body.searchType;
  if (st !== undefined && st !== null
      && (typeof st !== 'string' || !['title', 'description', 'both'].includes(st))) {
    throw invalidField('searchType', 'must be "title", "description", or "both"');
  }

  const since = body.discoveredSince;
  if (since !== undefined && since !== null
      && (typeof since !== 'string' || since.length > 64 || Number.isNaN(Date.parse(since)))) {
    throw invalidField('discoveredSince', 'must be a date and time');
  }
  return body;
}

// ---------------------------------------------------------------------------
// Licence + metering
// ---------------------------------------------------------------------------

async function authenticate(env, request) {
  const key = (request.headers.get('authorization') || '').replace(/^Bearer\s+/i, '').trim();
  if (!key) throw new HttpError(401, 'no_licence', 'Missing licence key');

  const row = await env.DB.prepare(
    `SELECT licence_key, tier, status, plan,
            max_postings_per_day, max_refreshes_per_day, expires_at
       FROM licences WHERE licence_key = ?1`
  ).bind(key).first();

  if (!row) throw new HttpError(403, 'unknown_licence', 'Licence not recognised');
  if (row.status !== 'active') {
    throw new HttpError(403, 'licence_inactive', `Licence is ${row.status}`);
  }
  // Checked here, on every request, rather than by a job that flips a status:
  // a job that fails or runs late leaves an expired licence working, and this
  // cannot. The same code the app already shows for an inactive licence, so it
  // needs no new handling to say so.
  if (hasExpired(row.expires_at)) {
    const ends = Date.parse(row.expires_at);
    throw new HttpError(403, 'licence_inactive', Number.isNaN(ends)
      ? 'Licence expiry date cannot be read'
      : `Licence expired on ${new Date(ends).toISOString().slice(0, 10)}`);
  }
  return row;
}

/**
 * The UTC day a request is metered against, read from the clock ONCE per
 * request and carried to every statement that meters it.
 *
 * The reservation is written before the upstream call and the refund after it.
 * Reading the clock for each would, for a search that straddles midnight,
 * reserve against one day and refund against the next — a row that does not
 * exist, so the refund would match nothing and the unused postings would stay
 * spent.
 */
function utcDay(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

async function usageOn(env, licenceKey, day) {
  const row = await env.DB.prepare(
    `SELECT refreshes, postings FROM usage_daily WHERE licence_key = ?1 AND day = ?2`
  ).bind(licenceKey, day).first();
  return row || { refreshes: 0, postings: 0 };
}

/**
 * Check and spend in ONE statement: one refresh and `?3` postings, or nothing.
 *
 * The INSERT branch carries its own guard because a licence with no row yet
 * today never reaches the DO UPDATE ... WHERE, and would otherwise be let
 * through whatever its caps say.
 */
const RESERVE_SQL = `
  INSERT INTO usage_daily (licence_key, day, refreshes, postings)
  SELECT ?1, ?2, 1, ?3 WHERE ?4 >= 1 AND ?3 <= ?5
  ON CONFLICT(licence_key, day) DO UPDATE SET
    refreshes = usage_daily.refreshes + 1,
    postings  = usage_daily.postings + excluded.postings
  WHERE usage_daily.refreshes < ?4
    AND usage_daily.postings + excluded.postings <= ?5
  RETURNING refreshes, postings`;

/**
 * How many times a reservation is retried when another request on the same
 * licence spends the headroom between the read and the write. Each retry
 * re-reads, so a licence that has genuinely run out is refused on the next
 * pass; only one whose allowance keeps moving exhausts these.
 */
const RESERVE_ATTEMPTS = 5;

/**
 * Reserve this search's share of today's allowance BEFORE anything upstream is
 * asked, and report what was used before it.
 *
 * WHY A RESERVATION. This used to read usage, call the provider, and record
 * what came back afterwards. Every request in flight read the same figure, so
 * parallel searches at 0 of 700 each passed and each fetched a full page — the
 * cap bounded any one request and not the requests together. The conditional
 * upsert checks and spends in a single statement, which D1 runs one at a time,
 * so the second request sees the first one's spend. What a fetch does not use
 * is refunded afterwards by `refundAllowance`.
 *
 * The reservation is clamped to what is left rather than refused, because a
 * request at 699/700 that fetched a further 100 rows once overshot the cap by
 * a whole page. Clamping lands the request that crosses the cap exactly on it.
 */
async function reserveAllowance(env, licence, day, asked) {
  const maxRefresh = licence.max_refreshes_per_day ?? DEFAULTS.maxRefreshesPerDay;
  const maxPostings = licence.max_postings_per_day ?? DEFAULTS.maxPostingsPerDay;

  for (let attempt = 0; attempt < RESERVE_ATTEMPTS; attempt++) {
    const used = await usageOn(env, licence.licence_key, day);
    if (used.refreshes >= maxRefresh) {
      throw new HttpError(429, 'refresh_cap', `Daily refresh cap reached (${maxRefresh})`);
    }
    if (used.postings >= maxPostings) {
      // Named in postings, with the numbers, because the app has to be able to
      // say "you are capped" in those words rather than showing an empty list
      // (spec 6.2). No upstream call is made, so this costs nothing.
      throw new HttpError(429, 'posting_cap',
        `Daily posting cap reached — ${used.postings} of ${maxPostings} fetched today`);
    }
    const reserved = Math.max(1, Math.min(asked, maxPostings - used.postings));
    const after = await env.DB.prepare(RESERVE_SQL)
      .bind(licence.licence_key, day, reserved, maxRefresh, maxPostings).first();
    if (after) {
      // From the row this statement wrote, not from the read above, which
      // another request may already have overtaken.
      const before = { refreshes: after.refreshes - 1, postings: after.postings - reserved };
      return {
        day, reserved, maxRefresh, maxPostings,
        used: before,
        headroom: maxPostings - before.postings,
      };
    }
  }
  throw new HttpError(503, 'usage_contended',
    'Too many searches are running on this licence at once; try again shortly');
}

/**
 * Give back what a reservation did not use, against the reservation's own day
 * and never below zero.
 *
 * A refund that fails is logged and swallowed rather than thrown. The licence
 * is then charged for postings it did not receive, which errs towards the cap;
 * throwing instead would turn a search whose rows were already paid for into a
 * 500 and discard those rows as well.
 */
async function refundAllowance(env, licenceKey, caps, { refreshes = 0, postings = 0 }) {
  const r = Math.max(0, refreshes);
  const p = Math.max(0, postings);
  if (r === 0 && p === 0) return;
  try {
    await env.DB.prepare(
      `UPDATE usage_daily
          SET refreshes = MAX(0, refreshes - ?3),
              postings  = MAX(0, postings - ?4)
        WHERE licence_key = ?1 AND day = ?2`
    ).bind(licenceKey, caps.day, r, p).run();
  } catch (err) {
    console.error('usage refund failed', { refreshes: r, postings: p, error: err?.name });
  }
}

// ---------------------------------------------------------------------------
// Provider selection — the kill switch and failover live in D1, not in code
// ---------------------------------------------------------------------------

async function activeProviders(env) {
  const { results } = await env.DB.prepare(
    `SELECT name, enabled, priority FROM providers WHERE enabled = 1 ORDER BY priority ASC`
  ).all();
  if (!results || results.length === 0) {
    throw new HttpError(503, 'no_provider', 'No feed provider is currently enabled');
  }
  return results;
}

const ADAPTERS = {
  /**
   * TheirStack. Behaviour measured in P0, not assumed:
   *   1 credit = 1 job RETURNED; a miss costs 0; job_title_or matches loosely;
   *   descriptions are full text; URLs are ATS-canonical.
   * `discovered_at_gte` is the delta pull, and it is mandatory — billing per
   * job returned means re-fetching yesterday's postings is re-buying them.
   */
  theirstack: {
    /** 1 credit per row returned: its rows draw on the licence's allowance. */
    billed: true,
    /** It filters by the feed's own place ids. */
    needsPlaces: true,
    /**
     * @param {number} headroom  postings reserved for this search. The page
     *                           size is clamped to it, so a fetch can never
     *                           bill past what the cap let it reserve.
     * @returns {{jobs: object[], total: number|null}} `total` is how many the
     *          query actually matched, so the caller can report what it chose
     *          not to fetch instead of presenting a clamped page as the lot.
     */
    async search(env, q, headroom) {
      // `maxResults` FIRST, because that is the name the application actually
      // sends. This read `q.limit` only, and the app has never sent that key —
      // it sends `maxResults` (app/feed/managed.py). So the user's own
      // "how many do you want" setting was received and silently discarded,
      // and every search fell back to MAX_PAGE.
      //
      // The cap still bounded the damage, which is why nothing looked wrong.
      // But on a feed billed per row returned, a control that quietly does
      // nothing is the expensive kind of defect: someone asks for 20 postings,
      // is billed for 100, and neither side has anything to point at.
      //
      // `limit` is still accepted so a hand-made request against the Worker
      // keeps working, but it is now the fallback rather than the only name.
      const asked = Math.max(1, Math.min(q.maxResults ?? q.limit ?? MAX_PAGE,
                                         headroom));
      const base = {
        // A closed posting cannot be applied for and is still billed.
        // Measured 2026-09-11: 504 of 1,756 UK registered-nurse postings in a
        // 30-day window (28.7%) had already closed.
        is_closed: false,
      };
      // searchType: "title" (default), "description", or "both".
      // Title-only is the cheapest shape: fewer rows per credit, higher
      // relevance. Description search is broader and costlier.
      const st = q.searchType || "title";
      if (q.countries?.length) base.job_country_code_or = q.countries;
      if (q.locationIds?.length) base.job_location_or = q.locationIds.map((id) => ({ id }));
      if (q.companies?.length) base.company_name_or = q.companies;
      if (q.excludeTitleTerms?.length) base.job_title_not = q.excludeTitleTerms;
      if (q.excludeCompanies?.length) base.company_name_not = q.excludeCompanies;
      if (q.descriptionKeywords?.length) base.job_description_contains_or = q.descriptionKeywords;
      // TheirStack requires at least one mandatory time or identity filter
      // (E-024). Always send posted_at_max_age_days so no request is rejected.
      base.posted_at_max_age_days = q.postedWithinDays || 30;
      if (q.discoveredSince) base.discovered_at_gte = q.discoveredSince;
      if (q.excludeJobIds?.length) base.job_id_not = q.excludeJobIds.map(Number);

      // "both" means title OR description. TheirStack ANDs different filter
      // groups in one request, so two separate requests merged by job id is
      // the only way to get a true OR.
      if (st === "both" && q.titles?.length) {
        const titleBase = { ...base, job_title_or: q.titles };
        const descBase  = { ...base };
        descBase.job_description_contains_or = [
          ...(base.job_description_contains_or || []),
          ...q.titles,
        ];
        const half = Math.max(1, Math.ceil(asked / 2));
        const [tRes, dRes] = await Promise.all([
          fetchPaged(titleBase, half, env),
          fetchPaged(descBase, half, env),
        ]);
        const seen = new Set();
        const merged = [];
        for (const job of [...tRes.jobs, ...dRes.jobs]) {
          if (!seen.has(job.provider_job_id)) {
            seen.add(job.provider_job_id);
            merged.push(job);
          }
        }
        const total = (tRes.total ?? 0) + (dRes.total ?? 0);
        return { jobs: merged.slice(0, asked), total };
      }

      // Single-mode: title or description, not both.
      if (q.titles?.length) {
        if (st === "title") base.job_title_or = q.titles;
        if (st === "description") {
          base.job_description_contains_or = [
            ...(base.job_description_contains_or || []),
            ...q.titles,
          ];
        }
      }
      return fetchPaged(base, asked, env);
    },
  },

  /** LinkedIn Job Library: see linkedin.js. Searched alongside TheirStack. */
  linkedin: makeLinkedInAdapter({ HttpError }),
};

function normaliseTheirStack(row) {
  return {
    provider: 'theirstack',
    provider_job_id: String(row.id ?? row.job_id ?? row.url ?? ''),
    title: row.job_title || '',
    company: row.company || row.company_object?.name || '',
    locations: [row.location, row.short_location, row.long_location].filter(Boolean),
    description_text: row.description || '',
    posted_at: row.date_posted || null,
    salary: row.salary_string || null,
    url: row.final_url || row.url || row.source_url || '',
    raw_criteria: {
      seniority: row.seniority ?? null,
      industry: row.industry ?? null,
      remote: row.remote ?? null,
      hybrid: row.hybrid ?? null,
      // Read by the app's scope gates, which may only remove a posting whose
      // own tag contradicts what the user stated, and keep it when blank.
      country_codes: row.country_codes ?? (row.country_code ? [row.country_code] : []),
      employment_statuses: row.employment_statuses ?? [],
    },
  };
}

// ---------------------------------------------------------------------------
// Places — a place name from the app, as the feed's own place ids
// ---------------------------------------------------------------------------

/**
 * Which catalogue entry to take when several share the exact name, best first.
 *
 * The first result is often not the place meant: for "Berlin" the city came
 * third, behind two regions, and for "Chicago" every leading result was a
 * region. Metro-area ids (RGNE) are never taken, because filtering by one
 * matched nothing at all (Chicago metro: 0 postings, Chicago city: 775).
 */
const PLACE_PREFERENCE = ['PPLC', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'ADM2',
  'PPL', 'ADM1', 'ADM3', 'PPLX'];

function pickPlace(rows, name) {
  const wanted = name.trim().toLowerCase();
  const rank = (code) => {
    const i = PLACE_PREFERENCE.indexOf(code);
    return i === -1 ? PLACE_PREFERENCE.length : i;
  };
  return rows
    .filter((r) => String(r.name || '').toLowerCase() === wanted
      && r.feature_code !== 'RGNE')
    .sort((a, b) => rank(a.feature_code) - rank(b.feature_code))[0] || null;
}

// A name the cache has not seen is looked up once per listed country, so 20
// cities across 250 countries is 5,000 upstream calls from one request — past
// Cloudflare's per-request limit and well past TheirStack's 50 an hour, which
// would then refuse everybody's searches for the rest of the hour. Counted
// against FETCHES, not places, so a search whose places are all cached is free
// however many it names.
const MAX_PLACE_LOOKUPS = 20;

async function resolvePlace(env, name, countries, budget) {
  const cache = caches.default;
  const key = new Request('https://cache.dawnlist.internal/place?n='
    + encodeURIComponent(name.trim().toLowerCase())
    + '&c=' + encodeURIComponent([...countries].sort().join(',')));
  const hit = await cache.match(key);
  if (hit) return (await hit.json()).id;

  for (const country of countries.length ? countries : [null]) {
    if (budget.spent >= MAX_PLACE_LOOKUPS) {
      throw new HttpError(400, 'too_many_place_lookups',
        'This search names too many places across too many countries to look up. '
        + 'Name fewer places, or fewer countries.');
    }
    budget.spent += 1;
    const url = new URL('https://api.theirstack.com/v0/catalog/locations');
    url.searchParams.set('name', name.trim());
    url.searchParams.set('limit', '25');
    if (country) url.searchParams.set('country_code', country);
    const res = await fetch(url, {
      headers: { authorization: `Bearer ${env.THEIRSTACK_API_KEY}` },
    });
    if (!res.ok) {
      const errBody = await res.text().catch(() => '');
      console.error('place lookup error', { status: res.status, body: errBody.slice(0, 200) });
      throw new HttpError(502, 'provider_error',
        `place lookup returned ${res.status}: ${errBody.slice(0, 200)}`);
    }
    const payload = await res.json();
    const rows = Array.isArray(payload) ? payload : (payload.data || payload.result || []);
    const place = pickPlace(rows, name);
    if (place) {
      // Found places only. A miss is not cached, because a transient empty
      // answer would otherwise refuse that place for a month.
      await cache.put(key, new Response(JSON.stringify({ id: place.id }), {
        headers: { 'cache-control': `max-age=${PLACE_TTL_SECONDS}`,
                   'content-type': 'application/json' },
      }));
      return place.id;
    }
  }
  return null;
}

/**
 * Cities whose everyday meaning is the whole county-level region round them,
 * with the region's place id, by country. AN EXPLICIT TABLE, NOT A NAME RULE.
 *
 * WHY. A posting carries ONE city id plus the county (ADM2) it is filed under,
 * and the feed matches an id against either. Searching "London" sent only the
 * city id, so every posting filed under a borough or district — Romford, West
 * Drayton (Heathrow), Hammersmith and Fulham, Richmond, Ilford, the City of
 * Westminster, Edgware, and some literally filed as "Greater London" — never
 * came back. Measured 2026-09-30, "hotel manager", 45 days: the London city id
 * returned 41, Greater London (ADM2 2648110) returned 53, and adding the City of
 * London on top still returned 53. All 12 missing were real London jobs, and
 * nothing said so: a smaller list reads as a quiet market.
 *
 * WHY NOT "look up Greater <name> for every city". Tried and REJECTED: a name
 * pattern widens searches nobody asked to widen, and the feed bills per row.
 * "Greater Sudbury" is a whole merged municipality, "Greater Noida" is a
 * different city, and a search must never be widened past what the user named
 * (see `resolvePlaces`). An entry belongs here only when someone has MEASURED
 * the city id against the region and confirmed the extra rows are the same
 * place under other names. Add one by running both counts, not by guessing.
 *
 * Keyed by the place NAME as the user gave it, lower-case. Applied only for the
 * countries the search names, so "London" with `CA` alone stays Ontario.
 */
const METRO_REGIONS = {
  GB: {
    // Each measured 2026-09-30 with tools/metro_candidates.py (a generic
    // "manager" query, 30 days): postings for the city id vs the region id.
    london: 2648110,      // Greater London: 17,035 vs 18,833 (x1.11); "hotel manager" 41 vs 53
    bournemouth: 12165737, // Bournemouth, Christchurch and Poole: 153 vs 291 (x1.9), one conurbation
    oldham: 3333179,      // Borough of Oldham: 55 vs 71 (x1.29)
    'st helens': 3333201, // St. Helens borough: 43 vs 58 (x1.35)
    wigan: 3333219,       // Borough of Wigan: 52 vs 74 (x1.42)
    // MEASURED AND DELIBERATELY NOT INCLUDED (the region is a different place):
    //   northampton -> West Northamptonshire (x1.24): also Daventry and Brackley.
    //   birkenhead  -> Wirral borough (x3.59): really all of Wirral.
    //   aberdeen    -> Aberdeen City (x1.07): nothing to gain.
  },
};

function regionIdsFor(name, countries) {
  const key = name.trim().toLowerCase();
  const ids = [];
  for (const country of countries) {
    const id = METRO_REGIONS[country]?.[key];
    if (id !== undefined && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

async function resolvePlaces(env, names, countries) {
  const ids = [];
  const budget = { spent: 0 };
  for (const name of names) {
    const id = await resolvePlace(env, name, countries, budget);
    if (id === null) {
      // Refused, never widened. Dropping the place would search the whole
      // country, and the user would pay for postings they had ruled out.
      throw new HttpError(400, 'unknown_location',
        `No place called "${name}" was found`
        + (countries.length ? ` in ${countries.join(', ')}` : ''));
    }
    if (!ids.includes(id)) ids.push(id);
    for (const region of regionIdsFor(name, countries)) {
      if (!ids.includes(region)) ids.push(region);
    }
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Cross-user cache
//
// Shared only between users served by the SAME Cloudflare data centre:
// `caches.default` is local to each one, so a search cached in London is a
// miss in Frankfurt and is fetched, and billed, again there.
// ---------------------------------------------------------------------------

function cacheKeyFor(query, provider = 'theirstack') {
  const canonical = JSON.stringify({
    // BUMP THIS WHEN A CHANGE ALTERS WHAT THE SAME QUERY RETURNS. Results are
    // kept for six hours and the key is the query, not how its places resolve,
    // so without a version a fix to place resolution (v2: Greater London is
    // searched with the London city id) would sit behind every result cached
    // before the deploy, and the tests would pass while nobody saw the change.
    v: 2,
    t: [...(query.titles || [])].sort(),
    c: [...(query.countries || [])].sort(),
    co: [...(query.companies || [])].sort(),
    d: query.postedWithinDays || null,
    s: query.discoveredSince || null,
    p: query.page || 0,
    // Everything that narrows a result is in the key. Without these, one
    // user's search for London, or with an employer excluded, would be served
    // to another user who asked for the whole country or that employer.
    ci: [...(query.cities || [])].map((c) => c.trim().toLowerCase()).sort(),
    xt: [...(query.excludeTitleTerms || [])].map((t) => t.trim().toLowerCase()).sort(),
    xc: [...(query.excludeCompanies || [])].sort(),
    dk: [...(query.descriptionKeywords || [])].map((k) => k.trim().toLowerCase()).sort(),
    m: query.maxResults ?? query.limit ?? null,
    st: query.searchType || "title",
  });
  // TheirStack's key is exactly what it was, so its cached entries stay valid;
  // any other provider is keyed apart so the two can never answer for each other.
  const suffix = provider === 'theirstack' ? '' : `&p=${encodeURIComponent(provider)}`;
  return new Request(`https://cache.dawnlist.internal/search?q=${encodeURIComponent(canonical)}${suffix}`);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

async function handleSearch(request, env, ctx) {
  const licence = await authenticate(env, request);
  const query = await readSearch(request);
  // BEFORE the reservation. A reservation is held until the search finishes, so
  // anything read while holding one shortens what a concurrent request can
  // reserve; a read that needs no allowance has no business inside that window.
  // It also means "no provider is enabled" is refused before anything is spent.
  const providers = await activeProviders(env);
  const caps = await reserveAllowance(env, licence, utcDay(),
    query.maxResults ?? query.limit ?? MAX_PAGE);

  let delivered;
  try {
    delivered = await deliverSearch(env, ctx, query, caps, providers);
  } catch (err) {
    // Nothing reached the user, so nothing is spent — not the postings and
    // not the refresh. An unknown place or a provider outage must not cost one
    // of the day's searches.
    await refundAllowance(env, licence.licence_key, caps,
      { refreshes: 1, postings: caps.reserved });
    throw err;
  }
  await refundAllowance(env, licence.licence_key, caps,
    { postings: caps.reserved - delivered.metered });
  return json(delivered.body);
}

/**
 * Every enabled provider is searched on every search, and the results are
 * combined. (It used to be FAILOVER: the first provider that answered won, so a
 * working TheirStack meant LinkedIn was never asked. The app then keeps the
 * richer copy of any role both returned; see `merge_same_run` in the app.)
 *
 * Providers differ in what a row COSTS, and everything that follows turns on it:
 *   billed    (TheirStack) 1 credit per row returned. Its rows draw on the
 *             licence's allowance, are cut to the reservation, have the user's
 *             held ids removed, and are not cached when the allowance or the
 *             held ids narrowed them.
 *   unbilled  (LinkedIn) free. Its rows never touch the allowance, are cached
 *             on their own, and one failing must not lose the other's rows.
 * Each provider is cached separately, so a free LinkedIn answer is never held
 * back by, or attributed to, a per-row-billed TheirStack one.
 */
async function deliverSearch(env, ctx, query, caps, enabled) {
  const cache = caches.default;
  const providers = enabled
    .map((p) => ({ name: p.name, adapter: ADAPTERS[p.name] }))
    .filter((p) => p.adapter);
  if (providers.length === 0) {
    throw new HttpError(503, 'no_provider', 'No feed provider is currently enabled');
  }

  // 1. What is already cached, per provider.
  const slots = await Promise.all(providers.map(async (p) => {
    const key = cacheKeyFor(query, p.name);
    const hit = await cache.match(key);
    return { ...p, key, cached: hit ? await hit.json() : null };
  }));

  // 2. Place ids, only when a provider that filters by them has to fetch — so a
  // fully cached search makes no place lookups, and an unknown place is still
  // refused before anything is paid for.
  if (query.cities?.length
      && slots.some((slot) => !slot.cached && slot.adapter.needsPlaces)) {
    query.locationIds = await resolvePlaces(env, query.cities, query.countries || []);
  }

  // 3. Fetch what is not cached, all at once. A provider that throws is
  // recorded and the others still deliver: degrade VISIBLY, never silently.
  const outcomes = await Promise.all(slots.map(async (slot) => {
    if (slot.cached) {
      return { ...slot, jobs: slot.cached.jobs, total: slot.cached.total, fromCache: true };
    }
    try {
      const budget = slot.adapter.billed
        ? caps.reserved
        : (query.maxResults ?? query.limit ?? MAX_PAGE);
      const result = await slot.adapter.search(env, query, budget);
      return { ...slot, ...result, fromCache: false };
    } catch (err) {
      return { ...slot, failed: { provider: slot.name, error: err.message, code: err.code } };
    }
  }));

  const failures = outcomes.filter((o) => o.failed).map((o) => o.failed);
  const answered = outcomes.filter((o) => !o.failed);
  if (answered.length === 0) {
    throw new HttpError(502, 'all_providers_failed',
      `every provider failed: ${failures.map((f) => f.provider).join(', ')}`);
  }

  // 4. Billed rows: the allowance's business.
  //
  // Meter on rows DELIVERED to this licence, not rows fetched upstream.
  // Spencer pays once per data centre for a shared result (the Cache API is
  // not global); each user still spends their own allowance on receiving it.
  // Metering the upstream fetch instead would let a licence draw unlimited
  // postings through the cache, and would make two users' caps depend on who
  // happened to run the query first.
  //
  // Rows this user already holds come out BEFORE the cut and the meter. A
  // fetch sends those ids to the feed so they are never returned or billed,
  // but a cached result was fetched for somebody else and still carries them.
  // Then cut to the reservation: what was asked for, clamped to what the
  // licence has left.
  const held = new Set(query.excludeJobIds || []);
  const billed = answered.filter((o) => o.adapter.billed);
  const billedJobs = billed
    .flatMap((o) => o.jobs)
    .filter((job) => !held.has(String(job.provider_job_id)))
    .slice(0, caps.reserved);
  const billedTotal = billed.length
    ? billed.reduce((sum, o) => sum + (typeof o.total === 'number' ? o.total : 0), 0)
    : null;
  // Unbilled rows: free, and never filtered by the held ids, which are the
  // billed feed's own numbering.
  const freeJobs = answered.filter((o) => !o.adapter.billed).flatMap((o) => o.jobs);

  // 5. Cache each fresh answer under its own key.
  for (const o of answered) {
    if (o.fromCache) continue;
    let cacheable;
    if (o.adapter.billed) {
      // Not when THIS licence's allowance cut the fetch short: the next user
      // asking the same search with allowance to spare would be served the
      // short list as if it were everything. Nor when THIS user's held ids
      // narrowed it: `job_id_not` keeps those postings out of the upstream
      // answer, so it is complete only for the user who asked.
      const cutByCap = o.jobs.length >= caps.headroom
        && !(typeof o.total === 'number' && o.total <= o.jobs.length);
      cacheable = !cutByCap && held.size === 0;
    } else {
      // A scan that ended on an error is a partial answer; do not keep it.
      cacheable = !o.partial;
    }
    if (cacheable) {
      ctx.waitUntil(cache.put(o.key, new Response(
        JSON.stringify({ jobs: o.jobs, total: o.total }), {
          headers: { 'cache-control': `max-age=${SEARCH_TTL_SECONDS}`,
                     'content-type': 'application/json' },
        })));
    }
  }

  const jobs = [...billedJobs, ...freeJobs];
  const allCached = answered.every((o) => o.fromCache);
  const fresh = answered.find((o) => !o.fromCache);
  return {
    jobs,
    // Only billed rows were metered; the refund of the unused reservation is
    // computed from this, not from `jobs.length`.
    metered: billedJobs.length,
    body: {
      jobs,
      cached: allCached,
      provider: allCached ? 'cache' : fresh.name,
      providers: Object.fromEntries(answered.map((o) => [o.name, {
        returned: o.adapter.billed ? billedJobs.length : o.jobs.length,
        matched: typeof o.total === 'number' ? o.total : null,
        cached: o.fromCache,
        billed: o.adapter.billed,
        scanned: o.scanned,
        exhausted: o.exhausted,
        partial: o.partial,
      }])),
      // Reported even when empty, so the app can tell "nothing new" from
      // "the fetch failed" (spec 6.2).
      degraded: failures.length ? failures : undefined,
      counts: funnel(billedJobs, caps, billedTotal, freeJobs.length),
    },
  };
}

/**
 * The funnel the app shows the user. `matched` vs `returned` is the honest
 * part: when a cap or a page limit stops us short, the response says how many
 * postings existed and how many were left unfetched, so the app can say
 * "your plan covers 100 of today's 340" rather than presenting a clamped page
 * as though it were everything (spec 6.2, 6.3).
 */
function funnel(jobs, caps, total = null, unbilled = 0) {
  const postingsUsed = caps.used.postings + jobs.length;
  const maxPostings = caps.maxPostings;
  const notFetched = (typeof total === 'number' && total > jobs.length)
    ? total - jobs.length
    : 0;
  return {
    matched: total,
    returned: jobs.length,
    not_fetched: notFetched,
    // True when the LICENCE CAP is what stopped us, as opposed to the query
    // simply matching fewer rows than the page size. The app must say so.
    capped: notFetched > 0 && jobs.length >= caps.headroom,
    refreshes_used: caps.used.refreshes + 1,
    refreshes_allowed: caps.maxRefresh,
    postings_used: postingsUsed,
    postings_allowed: maxPostings,
    postings_remaining: Math.max(0, maxPostings - postingsUsed),
    // Free rows from other providers, delivered on top of the metered ones.
    unbilled_returned: unbilled,
  };
}

async function handleHealth(env) {
  const providers = await activeProviders(env).catch(() => []);
  return json({ ok: true, providers: providers.map((p) => p.name) });
}

/**
 * Whether this licence is real, and — the load-bearing part — HOW IT WAS GOT.
 *
 * WHY THIS EXISTS AT ALL. `/health` answers for the SERVICE, not the caller:
 * it takes no request, reads no Authorization header, and returns 200 to
 * anybody. The desktop app was verifying licences against it — sending a
 * Bearer key and reading the status — so EVERY string typed into the licence
 * box verified, and `entitlement.check()` reported "licence verified" for all
 * of them. The metered routes still refused, so nothing paid was handed over,
 * but the gate was not a gate. Verification needs a route that authenticates.
 *
 * `granted_by_code` is what lets a Mac App Store build honour a comp code
 * without honouring a PURCHASE made outside Apple's commerce. Guideline 3.1.1
 * forbids unlocking purchased content with a key; a free grant is not a
 * purchase, and Wren already ships that distinction on Apple. The client
 * cannot tell one licence string from another, so the answer has to come from
 * here, where `licence_roles.from_code` and `licences.paddle_subscription_id`
 * actually record it.
 */
async function handleLicence(env, request) {
  const licence = await authenticate(env, request);
  const role = await env.DB.prepare(
    'SELECT role, from_code FROM licence_roles WHERE licence_key = ?1'
  ).bind(licence.licence_key).first();
  const origin = await env.DB.prepare(
    'SELECT paddle_subscription_id FROM licences WHERE licence_key = ?1'
  ).bind(licence.licence_key).first();

  return json({
    ok: true,
    status: licence.status,
    plan: licence.plan,
    role: role?.role || 'byo',
    granted_by_code: Boolean(role?.from_code),
    purchased: Boolean(origin?.paddle_subscription_id),
  });
}

/**
 * What plan this licence is on, what it allows, and what is left today.
 *
 * The app needs all three to say anything honest when a run is cut short. A
 * cap message that reads "limit reached" without the plan, the number and what
 * a higher plan would allow is not a report, it is a dead end — and spec 6.2
 * forbids presenting a truncated fetch as a finished one.
 *
 * Costs nothing upstream: every number here comes from D1.
 *
 * Note it returns the plan LADDER as well as the current plan, so the app does
 * not carry its own copy of the price list. A client-side plan table is a
 * second source of truth that goes stale the day a cap is revised, and the
 * caps here are already server-authoritative.
 */
async function handlePlan(request, env) {
  const licence = await authenticate(env, request);
  const used = await usageOn(env, licence.licence_key, utcDay());
  const maxPostings = licence.max_postings_per_day ?? DEFAULTS.maxPostingsPerDay;
  const maxRefresh = licence.max_refreshes_per_day ?? DEFAULTS.maxRefreshesPerDay;

  return json({
    ok: true,
    plan: licence.plan || null,
    // True when the caps came from the Worker fallback rather than a purchase.
    // Support needs to be able to tell those apart.
    plan_assigned: Boolean(licence.plan),
    tier: licence.tier,
    caps: { postings_per_day: maxPostings, refreshes_per_day: maxRefresh },
    used_today: { postings: used.postings, refreshes: used.refreshes },
    remaining_today: {
      postings: Math.max(0, maxPostings - used.postings),
      refreshes: Math.max(0, maxRefresh - used.refreshes),
    },
    // Plans with a price configured HERE only. This list is rendered as an
    // upgrade ladder: it once offered `trial`, and then offered Global while no
    // Global price existed, so the app advertised an upgrade no checkout sold.
    plans: sellablePlans(env).map((pl) => ({
      key: pl.key,
      postings_per_day: pl.maxPostingsPerDay,
      refreshes_per_day: pl.maxRefreshesPerDay,
    })),
    fallback_plan: FALLBACK_PLAN,
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/v1/search' && request.method === 'POST') {
        return await handleSearch(request, env, ctx);
      }
      if (url.pathname === '/v1/plan' && request.method === 'GET') {
        return await handlePlan(request, env);
      }
      if (url.pathname === '/v1/apple' && request.method === 'POST') return await handleApple(request, env);
      // The Microsoft Store's own till, beside Paddle rather than instead of
      // it. TWO routes because a Store ID key cannot be minted by the client
      // alone: it needs a service ticket from us first. src/microsoft.js has
      // the sequence.
      if (url.pathname === '/v1/microsoft/ticket' && request.method === 'GET') {
        return await handleTicket(env);
      }
      if (url.pathname === '/v1/microsoft' && request.method === 'POST') {
        return await handleMicrosoft(request, env);
      }
      if (url.pathname === '/redeem' && request.method === 'POST') {
        return await handleRedeem(request, env, newLicenceKey);
      }
      if (url.pathname.startsWith('/admin/')) {
        return await handleAdmin(request, env);
      }
      if (url.pathname === '/paddle/webhook' && request.method === 'POST') {
        return await handlePaddleWebhook(request, env, ctx);
      }
      if (url.pathname === '/health') {
        return await handleHealth(env);
      }
      // Verification, which /health cannot do: it never sees the request.
      if (url.pathname === '/v1/licence' && request.method === 'GET') {
        return await handleLicence(env, request);
      }
      throw new HttpError(404, 'not_found', 'No such route');
    } catch (err) {
      if (err instanceof HttpError) {
        return json({ error: err.code, message: err.message }, err.status);
      }
      // Never leak an upstream body to the client.
      console.error('unhandled', err?.name);
      return json({ error: 'internal', message: 'Unexpected error' }, 500);
    }
  },

  /**
   * The cron trigger in wrangler.jsonc. A retention period is only real if
   * something deletes on it; src/retention.js says what goes and what is
   * deliberately kept.
   */
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(purgeExpired(env));
  },
};
