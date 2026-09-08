/**
 * Dawnlist feed proxy.
 *
 * Sits between the app and both upstreams (the job-data provider and
 * Anthropic) so that:
 *   - the managed-tier user never sees an API key;
 *   - usage is metered per licence in D1 and fair-use caps are enforced;
 *   - query results are cached ACROSS users, so two people searching
 *     "hotel general manager, Dubai" share one upstream call;
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

import { newLicenceKey } from './paddle.js';
import { handlePaddleWebhook } from './paddle.js';
import { handleAdmin, handleRedeem } from './codes.js';
import { PLANS, FALLBACK_PLAN } from './plans.js';

const SEARCH_TTL_SECONDS = 6 * 60 * 60;   // 6h on search results
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
const MAX_PAGE = 100;

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

// ---------------------------------------------------------------------------
// Licence + metering
// ---------------------------------------------------------------------------

async function authenticate(env, request) {
  const key = (request.headers.get('authorization') || '').replace(/^Bearer\s+/i, '').trim();
  if (!key) throw new HttpError(401, 'no_licence', 'Missing licence key');

  const row = await env.DB.prepare(
    `SELECT licence_key, tier, status, plan,
            max_postings_per_day, max_refreshes_per_day
       FROM licences WHERE licence_key = ?1`
  ).bind(key).first();

  if (!row) throw new HttpError(403, 'unknown_licence', 'Licence not recognised');
  if (row.status !== 'active') {
    throw new HttpError(403, 'licence_inactive', `Licence is ${row.status}`);
  }
  return row;
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

async function usageToday(env, licenceKey) {
  const row = await env.DB.prepare(
    `SELECT refreshes, postings FROM usage_daily WHERE licence_key = ?1 AND day = ?2`
  ).bind(licenceKey, today()).first();
  return row || { refreshes: 0, postings: 0 };
}

async function recordUsage(env, licenceKey, { refreshes = 0, postings = 0 }) {
  await env.DB.prepare(
    `INSERT INTO usage_daily (licence_key, day, refreshes, postings)
     VALUES (?1, ?2, ?3, ?4)
     ON CONFLICT(licence_key, day) DO UPDATE SET
       refreshes = refreshes + excluded.refreshes,
       postings  = postings  + excluded.postings`
  ).bind(licenceKey, today(), refreshes, postings).run();
}

/**
 * Refuse what is already spent, and return the headroom that is left.
 *
 * Returning `headroom` is the point. The previous version only refused a
 * licence already AT the cap, which let a request at 699/700 fetch a further
 * 100 rows and bill for every one of them — the cap could be overshot by a
 * whole page on the request that crossed it. Callers must clamp their page
 * size to `headroom`.
 */
async function enforceCaps(env, licence) {
  const used = await usageToday(env, licence.licence_key);
  const maxRefresh = licence.max_refreshes_per_day ?? DEFAULTS.maxRefreshesPerDay;
  const maxPostings = licence.max_postings_per_day ?? DEFAULTS.maxPostingsPerDay;

  if (used.refreshes >= maxRefresh) {
    throw new HttpError(429, 'refresh_cap', `Daily refresh cap reached (${maxRefresh})`);
  }
  const headroom = maxPostings - used.postings;
  if (headroom <= 0) {
    // Named in postings, with the numbers, because the app has to be able to
    // say "you are capped" in those words rather than showing an empty list
    // (spec 6.2). No upstream call is made, so this costs nothing.
    throw new HttpError(429, 'posting_cap',
      `Daily posting cap reached — ${used.postings} of ${maxPostings} fetched today`);
  }
  return { used, maxRefresh, maxPostings, headroom };
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
    /**
     * @param {number} headroom  postings this licence may still be billed for
     *                           today. The page size is clamped to it, so the
     *                           cap can never be overshot by a partial page.
     * @returns {{jobs: object[], total: number|null}} `total` is how many the
     *          query actually matched, so the caller can report what it chose
     *          not to fetch instead of presenting a clamped page as the lot.
     */
    async search(env, q, headroom) {
      const limit = Math.max(1, Math.min(q.limit || MAX_PAGE, MAX_PAGE, headroom));
      const body = {
        limit,
        page: q.page || 0,
        // Free here, and it is what makes an honest cap possible: asking for
        // the total on the page we were fetching anyway costs no extra credit,
        // whereas a separate `limit=1` sizing call costs one (the docs are
        // explicit — "a count costs one record"). Never add that second call.
        include_total_results: true,
      };
      if (q.titles?.length) body.job_title_or = q.titles;
      if (q.countries?.length) body.job_country_code_or = q.countries;
      if (q.companies?.length) body.company_name_or = q.companies;
      if (q.postedWithinDays) body.posted_at_max_age_days = q.postedWithinDays;
      if (q.discoveredSince) body.discovered_at_gte = q.discoveredSince;
      // Excluding what we have already been billed for is a BILLING control,
      // not a nicety: TheirStack does not cache, so re-fetching a row we hold
      // re-buys it. It does NOT deduplicate the ATS and LinkedIn copies of one
      // job — those carry different ids and are billed twice regardless
      // (~7% of a UK hospitality pull, ~1.7% of a global one).
      if (q.excludeJobIds?.length) body.job_id_not = q.excludeJobIds;

      const res = await fetch('https://api.theirstack.com/v1/jobs/search', {
        method: 'POST',
        headers: {
          authorization: `Bearer ${env.THEIRSTACK_API_KEY}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        // Surfaced, never swallowed into "no new jobs".
        throw new HttpError(502, 'provider_error',
          `theirstack returned ${res.status}`);
      }
      const payload = await res.json();
      return {
        jobs: (payload.data || []).map(normaliseTheirStack),
        // Lives under `metadata`, not at the top level — reading it from the
        // top returns undefined silently and every page then looks unsized.
        total: payload.metadata?.total_results ?? null,
      };
    },
  },
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
    },
  };
}

// ---------------------------------------------------------------------------
// Cross-user cache
// ---------------------------------------------------------------------------

function cacheKeyFor(query) {
  const canonical = JSON.stringify({
    t: [...(query.titles || [])].sort(),
    c: [...(query.countries || [])].sort(),
    co: [...(query.companies || [])].sort(),
    d: query.postedWithinDays || null,
    s: query.discoveredSince || null,
    p: query.page || 0,
  });
  return new Request(`https://cache.dawnlist.internal/search?q=${encodeURIComponent(canonical)}`);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

async function handleSearch(request, env, ctx) {
  const licence = await authenticate(env, request);
  const caps = await enforceCaps(env, licence);
  const query = await request.json();

  const cache = caches.default;
  const cacheKey = cacheKeyFor(query);
  const hit = await cache.match(cacheKey);
  if (hit) {
    const cached = await hit.json();
    // Meter on rows DELIVERED to this licence, not rows fetched upstream.
    // Spencer pays once for a shared result; each user still spends their own
    // allowance on receiving it. Metering the upstream fetch instead would let
    // a licence draw unlimited postings through the cache, and would also make
    // two users' caps depend on who happened to run the query first.
    const jobs = cached.jobs.slice(0, caps.headroom);
    await recordUsage(env, licence.licence_key,
      { refreshes: 1, postings: jobs.length });
    return json({
      jobs,
      cached: true,
      provider: 'cache',
      counts: funnel(jobs, caps, cached.total),
    });
  }

  const providers = await activeProviders(env);
  let result = null;
  let usedProvider = null;
  const failures = [];

  for (const p of providers) {
    const adapter = ADAPTERS[p.name];
    if (!adapter) continue;
    try {
      result = await adapter.search(env, query, caps.headroom);
      usedProvider = p.name;
      break;
    } catch (err) {
      // Degrade to the next provider VISIBLY — the response says which failed.
      failures.push({ provider: p.name, error: err.message });
    }
  }

  if (result === null) {
    throw new HttpError(502, 'all_providers_failed',
      `every provider failed: ${failures.map((f) => f.provider).join(', ')}`);
  }

  const { jobs, total } = result;

  ctx.waitUntil(cache.put(cacheKey, new Response(JSON.stringify({ jobs, total }), {
    headers: { 'cache-control': `max-age=${SEARCH_TTL_SECONDS}`,
               'content-type': 'application/json' },
  })));

  await recordUsage(env, licence.licence_key,
    { refreshes: 1, postings: jobs.length });

  return json({
    jobs,
    cached: false,
    provider: usedProvider,
    // Reported even when empty, so the app can tell "nothing new" from
    // "the fetch failed" (spec 6.2).
    degraded: failures.length ? failures : undefined,
    counts: funnel(jobs, caps, total),
  });
}

/**
 * The funnel the app shows the user. `matched` vs `returned` is the honest
 * part: when a cap or a page limit stops us short, the response says how many
 * postings existed and how many were left unfetched, so the app can say
 * "your plan covers 100 of today's 340" rather than presenting a clamped page
 * as though it were everything (spec 6.2, 6.3).
 */
function funnel(jobs, caps, total = null) {
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
  };
}

async function handleHealth(env) {
  const providers = await activeProviders(env).catch(() => []);
  return json({ ok: true, providers: providers.map((p) => p.name) });
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
  const used = await usageToday(env, licence.licence_key);
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
    plans: Object.values(PLANS).map((pl) => ({
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
      if (url.pathname === '/redeem' && request.method === 'POST') {
        return await handleRedeem(request, env, newLicenceKey);
      }
      if (url.pathname.startsWith('/admin/')) {
        return await handleAdmin(request, env);
      }
      if (url.pathname === '/paddle/webhook' && request.method === 'POST') {
        return await handlePaddleWebhook(request, env);
      }
      if (url.pathname === '/health') {
        return await handleHealth(env);
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
};
