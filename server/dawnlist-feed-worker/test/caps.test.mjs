/**
 * The posting cap, against a real SQLite D1 and a stubbed cache and upstream.
 * Run: node test/caps.test.mjs
 *
 * What these protect, in one line each:
 *   - the cap is counted in postings FETCHED, which is the billable unit;
 *   - it cannot be overshot by the request that crosses it;
 *   - it cannot be overshot by requests that run AT THE SAME TIME;
 *   - what a search reserves and does not use is given back, on the same day;
 *   - being capped is always REPORTED, never shown as a short list;
 *   - a cache hit spends the receiving licence's allowance, not the fetcher's,
 *     and never more than the request asked for.
 */
import assert from 'node:assert';
import { makeSearchDB } from './search-db.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

// --- stubs -----------------------------------------------------------------

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
 * `delayMs` holds the response open so parallel requests genuinely overlap.
 */
function stubUpstream({ available = 500, delayMs = 0, status = 200, onCall = null }) {
  const seen = [];
  globalThis.fetch = async (_url, init) => {
    const body = JSON.parse(init.body);
    seen.push(body);
    if (onCall) onCall();
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
    if (status !== 200) return new Response('{}', { status });
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

const cachedJobs = (n) => ({
  jobs: Array.from({ length: n }, (_, i) => ({ provider_job_id: `j${i}` })), total: n,
});

const ctx = { waitUntil() {} };
const req = (body = {}) => new Request('https://w.example/v1/search', {
  method: 'POST',
  headers: { authorization: 'Bearer L1', 'content-type': 'application/json' },
  body: JSON.stringify(body),
});
const parallel = (n, body, DB) =>
  Promise.all(Array.from({ length: n }, () => worker.fetch(req(body), { DB }, ctx)));

const { default: worker } = await import('../src/index.js');

// --- tests -----------------------------------------------------------------

console.log('posting cap');

await test('clamps the page to remaining headroom, on the wire', async () => {
  const DB = makeSearchDB({ postings: 690, maxPostings: 700 });
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
  const DB = makeSearchDB({ postings: 699, maxPostings: 700 });
  makeCaches();
  stubUpstream({ available: 5000 });
  await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  assert.equal(DB.state.postings, 700);
});

await test('refuses at the cap without calling upstream at all', async () => {
  const DB = makeSearchDB({ postings: 700, maxPostings: 700 });
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
  const DB = makeSearchDB({ postings: 650, maxPostings: 700 });
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
  const DB = makeSearchDB({ postings: 0, maxPostings: 700 });
  makeCaches();
  stubUpstream({ available: 12 });
  const res = await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  const { counts } = await res.json();

  assert.equal(counts.returned, 12);
  assert.equal(counts.not_fetched, 0);
  assert.equal(counts.capped, false, 'a small result set is not a cap event');
  assert.equal(DB.state.postings, 12,
    'a hundred were reserved and eighty-eight given back');
});

console.log('\nrequests at the same time');

await test('parallel searches cannot overshoot the posting cap together', async () => {
  const DB = makeSearchDB({ maxPostings: 250, maxRefreshes: 10 });
  makeCaches();
  const seen = stubUpstream({ available: 500, delayMs: 5 });
  const responses = await parallel(5, { limit: 100 }, DB);
  const statuses = responses.map((r) => r.status).sort();
  const bodies = await Promise.all(responses.map((r) => r.json()));

  assert.deepEqual(statuses, [200, 200, 200, 429, 429]);
  assert.ok(bodies.filter((b) => b.error).every((b) => b.error === 'posting_cap'));
  assert.equal(seen.reduce((n, b) => n + b.limit, 0), 250,
    'upstream was never asked for more than the cap in total');
  assert.equal(DB.state.postings, 250);
});

await test('parallel searches cannot overshoot the refresh cap together', async () => {
  const DB = makeSearchDB({ maxPostings: 700, maxRefreshes: 2 });
  makeCaches();
  const seen = stubUpstream({ available: 500, delayMs: 5 });
  const responses = await parallel(4, { limit: 10 }, DB);
  const bodies = await Promise.all(responses.map((r) => r.json()));

  assert.equal(responses.filter((r) => r.status === 200).length, 2);
  assert.equal(bodies.filter((b) => b.error === 'refresh_cap').length, 2);
  assert.equal(seen.length, 2, 'the refused two cost no credit');
  assert.equal(DB.state.refreshes, 2);
});

await test('parallel cache hits are capped together too', async () => {
  const DB = makeSearchDB({ maxPostings: 250, maxRefreshes: 10 });
  makeCaches(cachedJobs(100));
  stubUpstream({ available: 500 });
  const responses = await parallel(5, {}, DB);
  const bodies = await Promise.all(responses.map((r) => r.json()));

  const delivered = bodies.reduce((n, b) => n + (b.jobs?.length || 0), 0);
  assert.equal(delivered, 250, 'the cache is not a way round the cap');
  assert.equal(DB.state.postings, 250);
});

await test('positive control: parallel searches within the allowance are all served', async () => {
  const DB = makeSearchDB({ maxPostings: 700 });
  makeCaches();
  stubUpstream({ available: 500, delayMs: 5 });
  const responses = await parallel(3, { limit: 100 }, DB);
  assert.deepEqual(responses.map((r) => r.status), [200, 200, 200]);
  assert.equal(DB.state.postings, 300);
  assert.equal(DB.state.refreshes, 3);
});

console.log('\nrefunds');

await test('a failed fetch spends nothing, not even the refresh', async () => {
  const DB = makeSearchDB({ maxPostings: 700 });
  makeCaches();
  stubUpstream({ status: 500 });
  const res = await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  assert.equal(res.status, 502);
  assert.deepEqual({ ...DB.state }, { refreshes: 0, postings: 0 });
});

await test('positive control: a successful search does spend its refresh', async () => {
  const DB = makeSearchDB({ maxPostings: 700 });
  makeCaches();
  stubUpstream({ available: 3 });
  await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  assert.deepEqual({ ...DB.state }, { refreshes: 1, postings: 3 });
});

await test('a search that crosses midnight is refunded on the day it reserved', async () => {
  const RealDate = Date;
  const setClock = (iso) => {
    const ms = RealDate.parse(iso);
    globalThis.Date = class extends RealDate {
      constructor(...a) { if (a.length) super(...a); else super(ms); }
      static now() { return ms; }
    };
  };
  try {
    const DB = makeSearchDB({ maxPostings: 700 });
    makeCaches();
    setClock('2026-09-10T23:59:59.500Z');
    stubUpstream({ available: 10, onCall: () => setClock('2026-09-11T00:00:00.500Z') });
    const res = await worker.fetch(req({ limit: 100 }), { DB }, ctx);
    assert.equal(res.status, 200);
    assert.deepEqual(DB.query('SELECT day, refreshes, postings FROM usage_daily'),
      [{ day: '2026-09-10', refreshes: 1, postings: 10 }],
      'the ninety unused postings came back to the day they were taken from');
  } finally {
    globalThis.Date = RealDate;
  }
});

console.log('\ncross-user cache');

await test('a cache hit spends the RECEIVING licence allowance', async () => {
  const DB = makeSearchDB({ postings: 0, maxPostings: 700 });
  makeCaches(cachedJobs(40));
  const seen = stubUpstream({ available: 500 });
  const res = await worker.fetch(req(), { DB }, ctx);
  const out = await res.json();

  assert.equal(out.cached, true);
  assert.equal(seen.length, 0, 'a cache hit costs no upstream credit');
  assert.equal(DB.state.postings, 40,
    'rows delivered still count, or the cache becomes an unmetered bypass');
});

await test('a cache hit is clamped to headroom like any other delivery', async () => {
  const DB = makeSearchDB({ postings: 690, maxPostings: 700 });
  makeCaches(cachedJobs(40));
  stubUpstream({ available: 500 });
  const res = await worker.fetch(req(), { DB }, ctx);
  const out = await res.json();

  assert.equal(out.jobs.length, 10);
  assert.equal(DB.state.postings, 700);
});

await test('a cache hit delivers no more than the request asked for', async () => {
  const DB = makeSearchDB({ postings: 0, maxPostings: 700 });
  makeCaches(cachedJobs(40));
  stubUpstream({ available: 500 });
  const res = await worker.fetch(req({ maxResults: 5 }), { DB }, ctx);
  const out = await res.json();

  assert.equal(out.jobs.length, 5, 'asked for five, handed forty');
  assert.equal(DB.state.postings, 5, 'and billed for five');
});

await test('a cache hit never bills for postings the user already holds', async () => {
  const DB = makeSearchDB({ maxPostings: 700 });
  makeCaches(cachedJobs(40));
  stubUpstream({ available: 500 });
  const res = await worker.fetch(
    req({ maxResults: 10, excludeJobIds: ['j0', 'j1', 'j2'] }), { DB }, ctx);
  const ids = (await res.json()).jobs.map((j) => j.provider_job_id);

  assert.ok(!ids.some((id) => ['j0', 'j1', 'j2'].includes(id)), `held rows delivered: ${ids}`);
  assert.equal(ids.length, 10, 'ten NEW postings, not seven');
  assert.equal(ids[0], 'j3');
  assert.equal(DB.state.postings, 10);
});

await test('held rows removed from a cache hit are refunded, not billed', async () => {
  const DB = makeSearchDB({ maxPostings: 700 });
  makeCaches(cachedJobs(40));
  stubUpstream({ available: 500 });
  const held = Array.from({ length: 36 }, (_, i) => `j${i}`);
  const res = await worker.fetch(req({ maxResults: 10, excludeJobIds: held }), { DB }, ctx);
  assert.equal((await res.json()).jobs.length, 4);
  assert.equal(DB.state.postings, 4, 'only the four the user did not hold are billed');
});

await test('positive control: without held ids the same cache hit starts at the first row', async () => {
  const DB = makeSearchDB({ maxPostings: 700 });
  makeCaches(cachedJobs(40));
  stubUpstream({ available: 500 });
  const res = await worker.fetch(req({ maxResults: 10 }), { DB }, ctx);
  const ids = (await res.json()).jobs.map((j) => j.provider_job_id);
  assert.equal(ids[0], 'j0');
  assert.equal(DB.state.postings, 10);
});

console.log('\nper-licence override');

await test('a licence column beats the Worker default, with no deploy', async () => {
  const DB = makeSearchDB({ postings: 0, maxPostings: 25 });
  makeCaches();
  const seen = stubUpstream({ available: 500 });
  await worker.fetch(req({ limit: 100 }), { DB }, ctx);
  assert.equal(seen[0].limit, 25, 'the D1 value is what reaches the provider');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
