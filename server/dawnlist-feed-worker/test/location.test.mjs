/**
 * Places, exclusions and paging, against a stubbed D1, cache and upstream.
 * Run: node test/location.test.mjs
 *
 * What these protect, in one line each:
 *   - a place reaches the feed as its place id, never as text the feed ignores;
 *   - an unknown place is refused before anything is paid for, never widened;
 *   - closed postings and the user's own exclusions are asked for at the feed;
 *   - a request larger than a page is paged, and stops when the feed runs out;
 *   - the country and contract tags the app's gates read are passed through;
 *   - one user's narrowed or cap-cut result is never served to another.
 */
import assert from 'node:assert';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

// --- stubs -----------------------------------------------------------------

function makeDB({ postings = 0, refreshes = 0, maxPostings = 700 } = {}) {
  const state = { postings, refreshes };
  return {
    state,
    prepare(sql) {
      return {
        _a: [],
        bind(...a) { this._a = a; return this; },
        async first() {
          if (sql.includes('FROM licences WHERE licence_key')) {
            return { licence_key: 'L1', tier: 'managed', status: 'active',
                     max_postings_per_day: maxPostings, max_refreshes_per_day: null };
          }
          if (sql.includes('FROM usage_daily')) {
            return { refreshes: state.refreshes, postings: state.postings };
          }
          return null;
        },
        async all() {
          if (sql.includes('FROM providers')) {
            return { results: [{ name: 'theirstack', enabled: 1, priority: 10 }] };
          }
          return { results: [] };
        },
        async run() {
          if (sql.includes('INSERT INTO usage_daily')) {
            const [, , r, p] = this._a;
            state.refreshes += r; state.postings += p;
          }
          return { success: true };
        },
      };
    },
  };
}

/** A cache keyed by URL, so different queries really are different entries. */
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

// Catalogue answers in the order the real catalogue gave them: the place
// meant is often NOT first.
const PLACES = {
  london: [
    { id: 2643741, name: 'City of London', feature_code: 'PPLA3' },
    { id: 2643743, name: 'London', feature_code: 'PPLC' },
    { id: 2643738, name: 'London Colney', feature_code: 'PPL' },
  ],
  berlin: [
    { id: 2950157, name: 'Berlin', feature_code: 'ADM1' },
    { id: 9999999, name: 'Berlin', feature_code: 'RGNE' },
    { id: 2950159, name: 'Berlin', feature_code: 'PPLC' },
  ],
};

function stubUpstream({ available = 500, row = null } = {}) {
  const calls = { search: [], places: [] };
  globalThis.fetch = async (url, init) => {
    const u = new URL(url);
    if (u.pathname === '/v0/catalog/locations') {
      calls.places.push(u.searchParams.get('name'));
      const rows = PLACES[u.searchParams.get('name').toLowerCase()] || [];
      return new Response(JSON.stringify(rows), { status: 200 });
    }
    const body = JSON.parse(init.body);
    calls.search.push(body);
    const start = body.offset || 0;
    const n = Math.max(0, Math.min(body.limit, available - start));
    return new Response(JSON.stringify({
      data: Array.from({ length: n }, (_, i) => ({
        id: `j${start + i}`, job_title: 'Role', company: 'Acme',
        description: 'x', url: `https://e.example/${start + i}`, ...(row || {}),
      })),
      metadata: { total_results: available },
    }), { status: 200 });
  };
  return calls;
}

const pending = [];
const ctx = { waitUntil(p) { pending.push(p); } };
const settle = () => Promise.all(pending.splice(0));
const req = (body = {}) => new Request('https://w.example/v1/search', {
  method: 'POST',
  headers: { authorization: 'Bearer L1', 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

const { default: worker } = await import('../src/index.js');

// --- places ----------------------------------------------------------------

console.log('places');

await test('a place reaches the feed as its place id, not as text', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 3 });
  const res = await worker.fetch(req({ titles: ['asset manager'],
    countries: ['GB'], cities: ['London'] }), { DB: makeDB() }, ctx);
  assert.equal(res.status, 200);
  assert.deepEqual(calls.search[0].job_location_or, [{ id: 2643743 }],
    'the city itself, not the City of London or London Colney');
  assert.deepEqual(calls.search[0].job_country_code_or, ['GB']);
});

await test('the city is taken over a region of the same name, and a metro id never', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 3 });
  await worker.fetch(req({ titles: ['marketing manager'], countries: ['DE'],
    cities: ['Berlin'] }), { DB: makeDB() }, ctx);
  assert.deepEqual(calls.search[0].job_location_or, [{ id: 2950159 }]);
});

await test('an unknown place is refused before anything is paid for', async () => {
  const DB = makeDB();
  makeCaches();
  const calls = stubUpstream();
  const res = await worker.fetch(req({ titles: ['nurse'], countries: ['GB'],
    cities: ['Atlantis'] }), { DB }, ctx);
  const out = await res.json();
  assert.equal(res.status, 400);
  assert.equal(out.error, 'unknown_location');
  assert.equal(calls.search.length, 0, 'no search was made, so nothing was billed');
  assert.equal(DB.state.refreshes, 0, 'and no refresh was spent');
  assert.equal(DB.state.postings, 0);
});

await test('a place is looked up once, not once per search', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 2 });
  await worker.fetch(req({ titles: ['a'], countries: ['GB'], cities: ['London'] }),
    { DB: makeDB() }, ctx);
  await settle();
  await worker.fetch(req({ titles: ['b'], countries: ['GB'], cities: ['London'] }),
    { DB: makeDB() }, ctx);
  assert.equal(calls.search.length, 2, 'two different searches each fetched');
  assert.equal(calls.places.length, 1, 'but the place was resolved only once');
});

// --- what is asked of the feed ----------------------------------------------

console.log('\nfeed filters');

await test('closed postings and the user\'s exclusions are excluded at the feed', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 1 });
  await worker.fetch(req({ titles: ['registered nurse'], countries: ['GB'],
    excludeTitleTerms: ['veterinary'], excludeCompanies: ['GIC'] }),
    { DB: makeDB() }, ctx);
  const body = calls.search[0];
  assert.equal(body.is_closed, false);
  assert.deepEqual(body.job_title_not, ['veterinary']);
  assert.deepEqual(body.company_name_not, ['GIC']);
});

await test('the country and contract tags reach the app for its gates', async () => {
  makeCaches();
  stubUpstream({ available: 1,
    row: { country_codes: ['GB'], employment_statuses: ['contract', 'full_time'] } });
  const res = await worker.fetch(req({ titles: ['a'], countries: ['GB'] }),
    { DB: makeDB() }, ctx);
  const { jobs } = await res.json();
  assert.deepEqual(jobs[0].raw_criteria.country_codes, ['GB']);
  assert.deepEqual(jobs[0].raw_criteria.employment_statuses, ['contract', 'full_time']);
});

// --- paging -----------------------------------------------------------------

console.log('\npaging');

await test('a request larger than a page is paged by offset, in one refresh', async () => {
  const DB = makeDB();
  makeCaches();
  const calls = stubUpstream({ available: 1000 });
  const res = await worker.fetch(req({ titles: ['a'], countries: ['GB'],
    maxResults: 250 }), { DB }, ctx);
  const out = await res.json();
  assert.deepEqual(calls.search.map((b) => b.offset), [0, 100, 200]);
  assert.deepEqual(calls.search.map((b) => b.limit), [100, 100, 50]);
  assert.equal(out.jobs.length, 250);
  assert.equal(DB.state.refreshes, 1, 'one search is one refresh however many pages');
  assert.equal(DB.state.postings, 250);
  assert.equal(calls.search[1].include_total_results, false,
    'the total is asked for on the first page only');
});

await test('paging stops when the feed runs out', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 130 });
  const res = await worker.fetch(req({ titles: ['a'], countries: ['GB'],
    maxResults: 500 }), { DB: makeDB() }, ctx);
  const out = await res.json();
  assert.equal(calls.search.length, 2);
  assert.equal(out.jobs.length, 130);
});

await test('the default is still one page, so nothing buys more unasked', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 1000 });
  await worker.fetch(req({ titles: ['a'], countries: ['GB'] }), { DB: makeDB() }, ctx);
  assert.equal(calls.search.length, 1);
  assert.equal(calls.search[0].limit, 100);
});

// --- the shared cache ---------------------------------------------------------

console.log('\ncache');

await test('two searches for different places do not share a result', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 3 });
  await worker.fetch(req({ titles: ['a'], countries: ['GB'], cities: ['London'] }),
    { DB: makeDB() }, ctx);
  await settle();
  const res = await worker.fetch(req({ titles: ['a'], countries: ['GB'] }),
    { DB: makeDB() }, ctx);
  const out = await res.json();
  assert.equal(out.cached, false, 'the whole-country search was not served London');
  assert.equal(calls.search.length, 2);
});

await test('a positive control: the same search twice IS served from cache', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 3 });
  await worker.fetch(req({ titles: ['a'], countries: ['GB'] }), { DB: makeDB() }, ctx);
  await settle();
  const res = await worker.fetch(req({ titles: ['a'], countries: ['GB'] }),
    { DB: makeDB() }, ctx);
  assert.equal((await res.json()).cached, true);
  assert.equal(calls.search.length, 1);
});

await test('a result cut short by one licence\'s allowance is not cached', async () => {
  makeCaches();
  const calls = stubUpstream({ available: 500 });
  await worker.fetch(req({ titles: ['a'], countries: ['GB'] }),
    { DB: makeDB({ postings: 690 }) }, ctx);
  await settle();
  const res = await worker.fetch(req({ titles: ['a'], countries: ['GB'] }),
    { DB: makeDB() }, ctx);
  const out = await res.json();
  assert.equal(out.cached, false, 'the second user was not handed ten rows as the lot');
  assert.equal(out.jobs.length, 100);
  assert.equal(calls.search.length, 2);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
