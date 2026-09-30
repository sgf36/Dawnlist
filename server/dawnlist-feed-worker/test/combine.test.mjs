/**
 * Both providers, searched together and combined, through the real entry point.
 * Run: node test/combine.test.mjs
 *
 * What these protect, in one line each:
 *   - TheirStack AND LinkedIn are searched on every search and both sets returned;
 *   - only TheirStack's rows (1 credit each) are metered against the allowance;
 *   - one provider failing degrades VISIBLY and never loses the other's rows;
 *   - each provider is cached under its own key, and a partial scan is not kept;
 *   - the guards that predate this still hold: held ids, unknown places, the cap.
 */
import assert from 'node:assert';
import { makeSearchDB } from './search-db.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

function makeCaches() {
  const store = new Map();
  globalThis.caches = {
    default: {
      async match(req) {
        const v = store.get(req.url);
        return v === undefined ? undefined
          : new Response(v, { headers: { 'content-type': 'application/json' } });
      },
      async put(req, res) { store.set(req.url, await res.text()); },
    },
  };
  return store;
}

const PLACES = { london: [{ id: 2643743, name: 'London', feature_code: 'PPLC' }] };

/** Routes every outbound call. Each provider can succeed, fail or be counted. */
function upstream({ ts = 3, li = 2, tsStatus = 200, liStatus = 200, liPartial = false, liIds = null } = {}) {
  const calls = { theirstack: 0, linkedin: 0, places: 0, tsBodies: [] };
  globalThis.fetch = async (url, init) => {
    const u = new URL(url);
    if (u.pathname === '/v0/catalog/locations') {
      calls.places++;
      return new Response(JSON.stringify(PLACES[u.searchParams.get('name').toLowerCase()] || []));
    }
    if (u.hostname === 'api.theirstack.com') {
      calls.theirstack++;
      calls.tsBodies.push(JSON.parse(init.body));
      if (tsStatus !== 200) return new Response('{}', { status: tsStatus });
      return new Response(JSON.stringify({
        data: Array.from({ length: ts }, (_, i) => ({
          id: 9000 + i, job_title: `Hotel Manager ${i}`, company: 'Acme',
          description: 'ts', url: `https://ts.example/${i}`, location: 'London, England',
        })),
        metadata: { total_results: ts },
      }));
    }
    if (u.hostname === 'api.linkedin.com') {
      calls.linkedin++;
      if (liStatus !== 200) return new Response('{"message":"no"}', { status: liStatus });
      const start = Number(u.searchParams.get('start'));
      if (liPartial && start >= 6 * 24) return new Response('{}', { status: 429 });
      const filler = (i) => ({
        jobPostingUrl: `https://www.linkedin.com/ad-library/job/detail/${4500000000 + start + i}`,
        jobDetails: { jobTitle: 'Cashier', jobLocation: 'Leeds, England', organizationName: 'Shop',
                      jobDescription: 'tills', jobListTimeInMilliseconds: Date.now() },
      });
      const rows = start > 0 ? [filler(0)] : Array.from({ length: li }, (_, i) => ({
        jobPostingUrl: `https://www.linkedin.com/ad-library/job/detail/${liIds ? liIds[i] : 4400000000 + i}`,
        jobDetails: {
          jobTitle: `Hotel Manager L${i}`, jobLocation: 'Croydon, England, United Kingdom',
          organizationName: 'Beta', jobDescription: 'li',
          jobListTimeInMilliseconds: Date.now() - 86400000,
        },
      }));
      return new Response(JSON.stringify({
        paging: { total: li, links: liPartial ? [{ rel: 'next' }] : (start === 0 ? [] : []) }, elements: rows,
      }));
    }
    throw new Error(`unexpected upstream call: ${url}`);
  };
  return calls;
}

const pending = [];
const ctx = { waitUntil(p) { pending.push(p); } };
const settle = () => Promise.all(pending.splice(0));
const req = (body = {}) => new Request('https://w.example/v1/search', {
  method: 'POST',
  headers: { authorization: 'Bearer L1', 'content-type': 'application/json' },
  body: JSON.stringify({ titles: ['hotel manager'], countries: ['GB'], cities: ['London'], maxResults: 50, ...body }),
});
const dbWith = (opts = {}, { linkedin = true } = {}) => {
  const DB = makeSearchDB(opts);
  if (linkedin) DB.sqlite.prepare("UPDATE providers SET enabled = 1 WHERE name = 'linkedin'").run();
  return DB;
};
const envOf = (DB) => ({ DB, LINKEDIN_ACCESS_TOKEN: 'tok' });
const { default: worker } = await import('../src/index.js');
const search = async (DB, body) => {
  const res = await worker.fetch(req(body), envOf(DB), ctx);
  await settle();
  return { res, out: await res.json() };
};

console.log('both providers');

await test('both are searched and both sets are returned, each row keeping its provider', async () => {
  makeCaches();
  const calls = upstream({ ts: 3, li: 2 });
  const { res, out } = await search(dbWith());
  assert.equal(res.status, 200);
  assert.equal(calls.theirstack, 1);
  assert.ok(calls.linkedin >= 1, 'LinkedIn was asked although TheirStack answered');
  const by = (p) => out.jobs.filter((j) => j.provider === p).length;
  assert.equal(by('theirstack'), 3);
  assert.equal(by('linkedin-joblibrary'), 2);
  assert.equal(out.providers.theirstack.billed, true);
  assert.equal(out.providers['linkedin']?.billed ?? out.providers.linkedin?.billed, false);
});

await test('LinkedIn is searched by the city name, TheirStack by place ids', async () => {
  makeCaches();
  const calls = upstream();
  const { out } = await search(dbWith());
  assert.ok(calls.tsBodies[0].job_location_or.some((l) => l.id === 2643743));
  assert.ok(out.jobs.some((j) => j.provider === 'linkedin-joblibrary' && j.locations[0].startsWith('Croydon')),
    'a Croydon posting counts as London');
});

await test('only TheirStack rows are metered against the allowance', async () => {
  makeCaches();
  upstream({ ts: 3, li: 2 });
  const DB = dbWith();
  const { out } = await search(DB);
  assert.equal(DB.state.postings, 3, 'LinkedIn rows are free and must not draw on the allowance');
  assert.equal(DB.state.refreshes, 1);
  assert.equal(out.counts.returned, 3);
  assert.equal(out.counts.unbilled_returned, 2);
  assert.equal(out.counts.postings_used, 3);
});

await test('with LinkedIn disabled in D1 only TheirStack is asked, as before', async () => {
  makeCaches();
  const calls = upstream();
  const { out } = await search(dbWith({}, { linkedin: false }));
  assert.equal(calls.linkedin, 0);
  assert.ok(out.jobs.every((j) => j.provider === 'theirstack'));
});

console.log('one provider failing');

await test('an expired LinkedIn token degrades visibly and TheirStack still delivers', async () => {
  makeCaches();
  upstream({ ts: 3, liStatus: 401 });
  const DB = dbWith();
  const { res, out } = await search(DB);
  assert.equal(res.status, 200);
  assert.equal(out.jobs.length, 3);
  assert.equal(out.degraded.length, 1);
  assert.equal(out.degraded[0].provider, 'linkedin');
  assert.equal(out.degraded[0].code, 'token_expired');
  assert.equal(DB.state.postings, 3);
});

await test('TheirStack down: LinkedIn still delivers, nothing is metered, and it says so', async () => {
  makeCaches();
  upstream({ tsStatus: 500, li: 2 });
  const DB = dbWith();
  const { res, out } = await search(DB);
  assert.equal(res.status, 200);
  assert.equal(out.jobs.length, 2);
  assert.ok(out.jobs.every((j) => j.provider === 'linkedin-joblibrary'));
  assert.equal(out.degraded[0].provider, 'theirstack');
  assert.equal(DB.state.postings, 0, 'no billed rows, so nothing metered');
});

await test('both down: refused as before, and nothing is spent, not even the refresh', async () => {
  makeCaches();
  upstream({ tsStatus: 500, liStatus: 500 });
  const DB = dbWith();
  const { res, out } = await search(DB);
  assert.equal(res.status, 502);
  assert.equal(out.error, 'all_providers_failed');
  assert.deepEqual({ ...DB.state }, { refreshes: 0, postings: 0 });
});

console.log('caching');

await test('a repeat is served from cache for BOTH providers, with no upstream call', async () => {
  makeCaches();
  const calls = upstream({ ts: 3, li: 2 });
  const DB = dbWith();
  await search(DB);
  const before = { ...calls };
  const { out } = await search(DB);
  assert.equal(calls.theirstack, before.theirstack);
  assert.equal(calls.linkedin, before.linkedin);
  assert.equal(out.cached, true);
  assert.equal(out.provider, 'cache');
  assert.equal(out.jobs.length, 5);
  // the allowance is still spent on what THIS licence received, but only billed rows
  assert.equal(DB.state.postings, 6);
});

await test('each provider is cached under its own key', async () => {
  const store = makeCaches();
  upstream();
  await search(dbWith());
  const keys = [...store.keys()].filter((k) => k.includes('/search?q='));
  assert.equal(keys.length, 2);
  assert.ok(keys.some((k) => k.includes('&p=linkedin')));
  assert.ok(keys.some((k) => !k.includes('&p=')), "TheirStack's key is the one it always was");
});

await test('a partial LinkedIn scan is not cached, so the next search tries again', async () => {
  const store = makeCaches();
  const calls = upstream({ liPartial: true, li: 2 });
  const DB = dbWith();
  const { out } = await search(DB, { maxResults: 500 });
  assert.ok(out.providers.linkedin?.partial ?? out.providers['linkedin']?.partial);
  assert.ok(![...store.keys()].some((k) => k.includes('&p=linkedin')), 'a partial scan was cached');
  const liBefore = calls.linkedin;
  await search(DB, { maxResults: 500 });
  assert.ok(calls.linkedin > liBefore, 'LinkedIn was asked again');
});

console.log('the guards that predate this');

await test("the user's held ids never filter LinkedIn rows (different numbering)", async () => {
  makeCaches();
  upstream({ ts: 1, li: 2, liIds: ['555', '556'] });
  const { out } = await search(dbWith(), { excludeJobIds: ['555'] });
  assert.equal(out.jobs.filter((j) => j.provider === 'linkedin-joblibrary').length, 2);
});

await test('an unknown place is refused before anything is asked or paid for', async () => {
  makeCaches();
  const calls = upstream();
  const DB = dbWith();
  const { res, out } = await search(DB, { cities: ['Atlantis'] });
  assert.equal(res.status, 400);
  assert.equal(out.error, 'unknown_location');
  assert.equal(calls.theirstack + calls.linkedin, 0);
  assert.deepEqual({ ...DB.state }, { refreshes: 0, postings: 0 });
});

await test('at the posting cap nothing is asked of either provider', async () => {
  makeCaches();
  const calls = upstream();
  const DB = dbWith({ postings: 700, maxPostings: 700 });
  const { res, out } = await search(DB);
  assert.equal(res.status, 429);
  assert.equal(out.error, 'posting_cap');
  assert.equal(calls.theirstack + calls.linkedin, 0);
});

await test('the reservation is refunded against BILLED rows, not everything delivered', async () => {
  makeCaches();
  upstream({ ts: 3, li: 20 });
  const DB = dbWith();
  await search(DB, { maxResults: 50 });
  assert.equal(DB.state.postings, 3, '20 free LinkedIn rows must not shrink the refund');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
