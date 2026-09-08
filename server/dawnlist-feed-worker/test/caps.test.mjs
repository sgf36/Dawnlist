/**
 * The posting cap, against a stubbed D1, cache and upstream.
 * Run: node test/caps.test.mjs
 *
 * What these protect, in one line each:
 *   - the cap is counted in postings FETCHED, which is the billable unit;
 *   - it cannot be overshot by the request that crosses it;
 *   - being capped is always REPORTED, never shown as a short list;
 *   - a cache hit spends the receiving licence's allowance, not the fetcher's.
 */
import assert from 'node:assert';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

// --- stubs -----------------------------------------------------------------

function makeDB({ postings = 0, refreshes = 0, maxPostings = null } = {}) {
  const state = { postings, refreshes, writes: [] };
  const db = {
    state,
    prepare(sql) {
      return {
        _a: [],
        bind(...a) { this._a = a; return this; },
        async first() {
          if (sql.includes('FROM licences WHERE licence_key')) {
            return {
              licence_key: 'L1', tier: 'managed', status: 'active',
              max_postings_per_day: maxPostings, max_refreshes_per_day: null,
            };
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
            state.writes.push({ refreshes: r, postings: p });
          }
          return { success: true };
        },
      };
    },
  };
  return db;
}

/** Cache stub. `seed` pre-populates the single slot. */
function makeCaches(seed = null) {
  let slot = seed;
  globalThis.caches = {
    default: {
      async match() {
        return slot === null ? undefined
          : new Response(JSON.stringify(slot), { headers: { 'content-type': 'application/json' } });
      },
      async put(_k, res) { slot = await res.json(); },
    },
  };
  return { get slot() { return slot; } };
}

/**
 * Upstream stub. Records the request body so the test can assert on the page
 * size actually asked for — the clamp is only real if it reaches the wire.
 */
function stubUpstream({ available = 500 }) {
  const seen = [];
  globalThis.fetch = async (_url, init) => {
    const body = JSON.parse(init.body);
    seen.push(body);
    const n = Math.min(body.limit, available);
    return new Response(JSON.stringify({
      data: Array.from({ length: n }, (_, i) => ({
        id: `j${i}`, job_title: `Role ${i}`, company: 'Acme',
        description: 'x', url: `https://e.example/${i}`, date_posted: '2026-09-07',
      })),
      metadata: { total_results: available },
    }), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  return seen;
}

const ctx = { waitUntil() {} };
const req = (body = {}) => new Request('https://w.example/v1/search', {
  method: 'POST',
  headers: { authorization: 'Bearer L1', 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

const { default: worker } = await import('../src/index.js');

// --- tests -----------------------------------------------------------------

console.log('posting cap');

await test('clamps the page to remaining headroom, on the wire', async () => {
  const DB = makeDB({ postings: 690, maxPostings: 700 });
  makeCaches();
  const seen = stubUpstream({ available: 500 });
  const res = await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  const out = await res.json();

  assert.equal(seen.length, 1, 'one upstream call');
  assert.equal(seen[0].limit, 10, `asked upstream for ${seen[0].limit}, want 10`);
  assert.equal(out.jobs.length, 10);
  assert.equal(DB.state.postings, 700, 'cap is reached exactly, never passed');
});

await test('never bills past the cap even with a large page requested', async () => {
  const DB = makeDB({ postings: 699, maxPostings: 700 });
  makeCaches();
  stubUpstream({ available: 5000 });
  await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  assert.equal(DB.state.postings, 700);
});

await test('refuses at the cap without calling upstream at all', async () => {
  const DB = makeDB({ postings: 700, maxPostings: 700 });
  makeCaches();
  const seen = stubUpstream({ available: 500 });
  const res = await worker.fetch(req(), { DB }, ctx);
  const out = await res.json();

  assert.equal(res.status, 429);
  assert.equal(out.error, 'posting_cap');
  assert.equal(seen.length, 0, 'a refusal must cost no credit');
  assert.match(out.message, /700 of 700/, 'says the numbers, not just "capped"');
});

await test('reports what it did NOT fetch, and says it was the cap', async () => {
  const DB = makeDB({ postings: 650, maxPostings: 700 });
  makeCaches();
  stubUpstream({ available: 340 });
  const res = await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  const { counts } = await res.json();

  assert.equal(counts.matched, 340);
  assert.equal(counts.returned, 50);
  assert.equal(counts.not_fetched, 290, 'the user is told 290 exist and were skipped');
  assert.equal(counts.capped, true);
  assert.equal(counts.postings_remaining, 0);
});

await test('a short page that is NOT the cap does not claim to be capped', async () => {
  const DB = makeDB({ postings: 0, maxPostings: 700 });
  makeCaches();
  stubUpstream({ available: 12 });
  const res = await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  const { counts } = await res.json();

  assert.equal(counts.returned, 12);
  assert.equal(counts.not_fetched, 0);
  assert.equal(counts.capped, false, 'a small result set is not a cap event');
});

console.log('cross-user cache');

await test('a cache hit spends the RECEIVING licence allowance', async () => {
  const DB = makeDB({ postings: 0, maxPostings: 700 });
  makeCaches({ jobs: Array.from({ length: 40 }, (_, i) => ({ provider_job_id: `j${i}` })), total: 40 });
  const seen = stubUpstream({ available: 500 });
  const res = await worker.fetch(req(), { DB }, ctx);
  const out = await res.json();

  assert.equal(out.cached, true);
  assert.equal(seen.length, 0, 'a cache hit costs no upstream credit');
  assert.equal(DB.state.postings, 40,
    'rows delivered still count, or the cache becomes an unmetered bypass');
});

await test('a cache hit is clamped to headroom like any other delivery', async () => {
  const DB = makeDB({ postings: 690, maxPostings: 700 });
  makeCaches({ jobs: Array.from({ length: 40 }, (_, i) => ({ provider_job_id: `j${i}` })), total: 40 });
  stubUpstream({ available: 500 });
  const res = await worker.fetch(req(), { DB }, ctx);
  const out = await res.json();

  assert.equal(out.jobs.length, 10);
  assert.equal(DB.state.postings, 700);
});

console.log('per-licence override');

await test('a licence column beats the Worker default, with no deploy', async () => {
  const DB = makeDB({ postings: 0, maxPostings: 25 });
  makeCaches();
  const seen = stubUpstream({ available: 500 });
  await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  assert.equal(seen[0].limit, 25, 'the D1 value is what reaches the provider');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
