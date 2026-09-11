/**
 * The page size actually asked for, on the wire.
 * Run: node test/limit.test.mjs
 *
 * The Worker read `q.limit`; the application sends `maxResults`. So the user's
 * own "how many postings do you want" setting was received and discarded, and
 * every search fell back to the 100-row page cap. The daily cap still bounded
 * the damage, which is why nothing ever looked wrong — but on a feed billed per
 * row RETURNED, a control that quietly does nothing is the expensive kind.
 *
 * These tests used to check a COPY of the clamp expression. A copy keeps
 * passing after the real one changes, which is exactly how the original bug
 * survived, so every test here now sends a request through the Worker's
 * `fetch` and reads the page size the provider was asked for.
 *
 * These tests are named for the money rather than for the field.
 */
import assert from 'node:assert';
import { makeSearchDB } from './search-db.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const MAX_PAGE = 100;
globalThis.caches = { default: { async match() {}, async put() {} } };

/** Returns the `limit` of every page the provider was asked for. */
function stubUpstream() {
  const limits = [];
  globalThis.fetch = async (_url, init) => {
    const { limit } = JSON.parse(init.body);
    limits.push(limit);
    return new Response(JSON.stringify({
      data: Array.from({ length: limit }, (_, i) => ({ id: `j${i}` })),
      metadata: { total_results: 5000 },
    }));
  };
  return limits;
}

const { default: worker } = await import('../src/index.js');

async function asked(body, db = {}) {
  const limits = stubUpstream();
  const DB = makeSearchDB(db);
  const res = await worker.fetch(new Request('https://w.example/v1/search', {
    method: 'POST',
    headers: { authorization: 'Bearer L1', 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }), { DB }, { waitUntil() {} });
  return { limits, res, DB };
}

console.log('page size');

await test('the number the APP sends is the number used', async () => {
  const { limits } = await asked({ maxResults: 20 });
  assert.deepEqual(limits, [20], 'maxResults is the name app/feed/managed.py actually sends');
});

await test('asking for twenty does not buy a hundred', async () => {
  const { DB } = await asked({ maxResults: 20 });
  assert.equal(DB.state.postings, 20);
  assert.notEqual(DB.state.postings, MAX_PAGE);
});

await test('a hand-made request using `limit` still works', async () => {
  assert.deepEqual((await asked({ limit: 5 })).limits, [5]);
});

await test('maxResults wins when both are present', async () => {
  assert.deepEqual((await asked({ maxResults: 10, limit: 90 })).limits, [10]);
});

await test('neither given falls back to the page cap', async () => {
  assert.deepEqual((await asked({})).limits, [MAX_PAGE]);
});

await test('headroom always wins, so a cap cannot be overshot', async () => {
  const { limits, DB } = await asked({ maxResults: 100 }, { postings: 697, maxPostings: 700 });
  assert.deepEqual(limits, [3]);
  assert.equal(DB.state.postings, 700);
});

await test('a spent allowance is refused without asking the provider for anything', async () => {
  const { limits, res } = await asked({ maxResults: 50 }, { postings: 700, maxPostings: 700 });
  assert.equal(res.status, 429);
  assert.deepEqual(limits, [], 'no page of any size, not even one row');
});

await test('a nonsense zero from a client is refused, and never becomes the page cap', async () => {
  // `q.limit || MAX_PAGE` once turned 0 into 100. It is now refused outright.
  const { limits, res } = await asked({ maxResults: 0 });
  assert.equal(res.status, 400);
  assert.deepEqual(limits, []);
  // Positive control: the smallest real request reaches the wire as itself.
  assert.deepEqual((await asked({ maxResults: 1 })).limits, [1]);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
