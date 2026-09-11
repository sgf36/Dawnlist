/**
 * Request bodies are checked before anything is reserved or fetched.
 * Run: node test/validation.test.mjs
 *
 * A body the Worker could not use used to reach code that assumed its shape:
 * `null` as a search body threw on `query.cities`, and a number as a redeem
 * code threw on `toUpperCase`. Both were 500s — indistinguishable, to the app,
 * from the Worker being down — and a search could reserve allowance before it
 * failed. These tests drive the real `fetch` export, because a validator that
 * exists and is not on the route is the shape of the bug.
 */
import assert from 'node:assert';
import { makeD1 } from './d1.mjs';
import { makeSearchDB } from './search-db.mjs';
import { seedCode } from './seed.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

globalThis.caches = { default: { async match() {}, async put() {} } };

function stubUpstream() {
  const calls = { search: 0, places: 0 };
  globalThis.fetch = async (url, init) => {
    if (new URL(url).pathname === '/v0/catalog/locations') {
      calls.places++;
      return new Response(JSON.stringify([{ id: 2643743, name: 'London', feature_code: 'PPLC' }]));
    }
    calls.search++;
    const { limit } = JSON.parse(init.body);
    return new Response(JSON.stringify({
      data: Array.from({ length: Math.min(limit, 3) }, (_, i) => ({ id: `j${i}` })),
      metadata: { total_results: 3 },
    }));
  };
  return calls;
}

const ctx = { waitUntil() {} };
const search = (raw) => new Request('https://w.example/v1/search', {
  method: 'POST',
  headers: { authorization: 'Bearer L1', 'content-type': 'application/json' },
  body: typeof raw === 'string' ? raw : JSON.stringify(raw),
});

const { default: worker } = await import('../src/index.js');

/** Sends a search and asserts it was refused with `code` having cost nothing. */
async function refused(body, code, fieldPattern = null) {
  const DB = makeSearchDB();
  const calls = stubUpstream();
  const res = await worker.fetch(search(body), { DB }, ctx);
  const out = await res.json();
  assert.equal(res.status, 400, `want 400 for ${JSON.stringify(body)?.slice(0, 60)}, got ${res.status}`);
  assert.equal(out.error, code);
  if (fieldPattern) assert.match(out.message, fieldPattern);
  assert.equal(calls.search + calls.places, 0, 'nothing upstream was asked');
  assert.deepEqual({ ...DB.state }, { refreshes: 0, postings: 0 }, 'nothing was reserved');
}

async function accepted(body) {
  const DB = makeSearchDB();
  stubUpstream();
  const res = await worker.fetch(search(body), { DB }, ctx);
  assert.equal(res.status, 200, `want 200, got ${res.status}: ${await res.clone().text()}`);
  return res.json();
}

/** Exactly what app/feed/managed.py sends, nulls included. */
const APP_BODY = {
  label: 'Nurses, London',
  titles: ['registered nurse'],
  countries: ['GB'],
  companies: [],
  postedWithinDays: 45,
  discoveredSince: '2026-09-10T08:00:00.123456',
  excludeJobIds: Array.from({ length: 1000 }, (_, i) => String(9000000 + i)),
  maxResults: 100,
  cities: ['London'],
  excludeTitleTerms: ['veterinary'],
  excludeCompanies: ['GIC'],
};

console.log('search body');

await test('positive control: the body the app actually sends is served', async () => {
  await accepted(APP_BODY);
});

await test('the app\'s nulls are accepted, not treated as values', async () => {
  await accepted({ ...APP_BODY, postedWithinDays: null, discoveredSince: null, maxResults: null });
});

await test('unreadable JSON is a 400, not a 500', async () => {
  await refused('{"titles": [', 'bad_json');
});

await test('JSON that is not an object is refused', async () => {
  for (const body of ['null', '[]', '42', '"nurse"']) await refused(body, 'invalid_body');
});

await test('maxResults must be a whole number in range', async () => {
  for (const bad of [0, -1, 1001, 2.5, '20', true]) {
    await refused({ ...APP_BODY, maxResults: bad }, 'invalid_field', /maxResults/);
  }
  await refused({ titles: ['a'], limit: 0 }, 'invalid_field', /limit/);
});

await test('positive control: the ends of the maxResults range are accepted', async () => {
  await accepted({ ...APP_BODY, maxResults: 1 });
  await accepted({ ...APP_BODY, maxResults: 1000 });
});

await test('excludeJobIds is capped at 2,000 strings', async () => {
  const ids = (n) => Array.from({ length: n }, (_, i) => String(i));
  await refused({ ...APP_BODY, excludeJobIds: ids(2001) }, 'invalid_field', /excludeJobIds/);
  await refused({ ...APP_BODY, excludeJobIds: [123] }, 'invalid_field', /excludeJobIds/);
  await accepted({ ...APP_BODY, excludeJobIds: ids(2000) });
});

await test('list fields must be lists of non-blank strings', async () => {
  await refused({ ...APP_BODY, titles: 'nurse' }, 'invalid_field', /titles/);
  await refused({ ...APP_BODY, titles: [7] }, 'invalid_field', /titles/);
  await refused({ ...APP_BODY, countries: [' '] }, 'invalid_field', /countries/);
  await refused({ ...APP_BODY, excludeCompanies: [{}] }, 'invalid_field', /excludeCompanies/);
});

await test('cities are capped in number and length, because each can cost a lookup', async () => {
  await refused({ ...APP_BODY, cities: Array.from({ length: 21 }, (_, i) => `Town ${i}`) },
    'invalid_field', /cities/);
  await refused({ ...APP_BODY, cities: ['x'.repeat(101)] }, 'invalid_field', /cities/);
});

await test('postedWithinDays and discoveredSince are checked', async () => {
  await refused({ ...APP_BODY, postedWithinDays: 0 }, 'invalid_field', /postedWithinDays/);
  await refused({ ...APP_BODY, postedWithinDays: '7' }, 'invalid_field', /postedWithinDays/);
  await refused({ ...APP_BODY, discoveredSince: 'yesterday-ish' }, 'invalid_field', /discoveredSince/);
  await refused({ ...APP_BODY, discoveredSince: 20260910 }, 'invalid_field', /discoveredSince/);
});

console.log('\nredeem body');

async function redeem(raw) {
  const DB = makeD1();
  const code = seedCode(DB);
  const body = typeof raw === 'function' ? raw(code.code) : raw;
  const res = await worker.fetch(new Request('https://w.example/redeem', {
    method: 'POST',
    headers: { 'cf-connecting-ip': '1.2.3.4', 'content-type': 'application/json' },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  }), { DB }, ctx);
  return { status: res.status, out: await res.json(), DB };
}

await test('a redeem body that is not an object is a 400, not a 500', async () => {
  for (const body of ['null', '[]', '7']) {
    const { status, out } = await redeem(body);
    assert.equal(status, 400);
    assert.equal(out.error, 'invalid_body');
  }
});

await test('a code that is not a string, or absurdly long, is a 400', async () => {
  for (const code of [123, { a: 1 }, 'D'.repeat(65)]) {
    const { status, out } = await redeem({ code });
    assert.equal(status, 400, `code ${JSON.stringify(code).slice(0, 20)} gave ${status}`);
    assert.equal(out.error, 'invalid_field');
  }
});

await test('positive control: a real code in a valid body still redeems', async () => {
  const { status, out } = await redeem((code) => ({ code }));
  assert.equal(status, 200, JSON.stringify(out));
  assert.ok(out.licence_key);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
