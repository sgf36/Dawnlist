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
 * Logging rule, and it is load-bearing for the store privacy labels: log
 * COUNTS, never CONTENT. No job descriptions, no CVs, no queries with personal
 * text are ever written to a log line.
 */

const SEARCH_TTL_SECONDS = 6 * 60 * 60;   // 6h on search results
const DETAIL_TTL_SECONDS = 7 * 24 * 60 * 60;

const DEFAULTS = {
  maxSavedQueries: 10,
  maxRefreshesPerDay: 3,
  maxPostingsPerDay: 600,      // soft cap at the screen's input
};

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
    `SELECT licence_key, tier, status, max_postings_per_day, max_refreshes_per_day
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

async function enforceCaps(env, licence) {
  const used = await usageToday(env, licence.licence_key);
  const maxRefresh = licence.max_refreshes_per_day ?? DEFAULTS.maxRefreshesPerDay;
  const maxPostings = licence.max_postings_per_day ?? DEFAULTS.maxPostingsPerDay;

  if (used.refreshes >= maxRefresh) {
    throw new HttpError(429, 'refresh_cap', `Daily refresh cap reached (${maxRefresh})`);
  }
  if (used.postings >= maxPostings) {
    throw new HttpError(429, 'posting_cap', `Daily posting cap reached (${maxPostings})`);
  }
  return { used, maxRefresh, maxPostings };
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
    async search(env, q) {
      const body = {
        limit: Math.min(q.limit || 100, 100),
        page: q.page || 0,
        include_total_results: false,
      };
      if (q.titles?.length) body.job_title_or = q.titles;
      if (q.countries?.length) body.job_country_code_or = q.countries;
      if (q.companies?.length) body.company_name_or = q.companies;
      if (q.postedWithinDays) body.posted_at_max_age_days = q.postedWithinDays;
      if (q.discoveredSince) body.discovered_at_gte = q.discoveredSince;

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
      return (payload.data || []).map(normaliseTheirStack);
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
    const jobs = await hit.json();
    // A cache hit costs no upstream credit, so it counts as a refresh but not
    // as billable postings.
    await recordUsage(env, licence.licence_key, { refreshes: 1 });
    return json({ jobs, cached: true, provider: 'cache', counts: funnel(jobs, caps) });
  }

  const providers = await activeProviders(env);
  let jobs = null;
  let usedProvider = null;
  const failures = [];

  for (const p of providers) {
    const adapter = ADAPTERS[p.name];
    if (!adapter) continue;
    try {
      jobs = await adapter.search(env, query);
      usedProvider = p.name;
      break;
    } catch (err) {
      // Degrade to the next provider VISIBLY — the response says which failed.
      failures.push({ provider: p.name, error: err.message });
    }
  }

  if (jobs === null) {
    throw new HttpError(502, 'all_providers_failed',
      `every provider failed: ${failures.map((f) => f.provider).join(', ')}`);
  }

  ctx.waitUntil(cache.put(cacheKey, new Response(JSON.stringify(jobs), {
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
    counts: funnel(jobs, caps),
  });
}

function funnel(jobs, caps) {
  return {
    returned: jobs.length,
    refreshes_used: caps.used.refreshes + 1,
    refreshes_allowed: caps.maxRefresh,
    postings_used: caps.used.postings + jobs.length,
    postings_allowed: caps.maxPostings,
  };
}

async function handleHealth(env) {
  const providers = await activeProviders(env).catch(() => []);
  return json({ ok: true, providers: providers.map((p) => p.name) });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/v1/search' && request.method === 'POST') {
        return await handleSearch(request, env, ctx);
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
