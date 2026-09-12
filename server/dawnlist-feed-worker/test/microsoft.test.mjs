/**
 * `/v1/microsoft` — a Store subscription, confirmed WITH MICROSOFT.
 * Run: node test/microsoft.test.mjs
 *
 * Against the real sqlite harness, not a SQL-matching stub: the licence rows
 * these write are the thing under test, and a fake that agrees with its author
 * proves nothing about them.
 *
 * What each protects, in one line:
 *   - the app's own opinion never reaches here; only a Store ID key does;
 *   - a missing credential is 503 and never a refusal, because the app reads
 *     5xx as "could not ask" and 403 as "you did not pay";
 *   - a refused collections query names the Partner Center registration,
 *     because it otherwise reads as "this customer owns nothing";
 *   - a lapsed subscription expires ITS licence and nothing else;
 *   - one customer maps to one licence however many times they ask.
 */
import assert from 'node:assert';
import worker from '../src/index.js';
import { makeD1 } from './d1.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const PRODUCT = '9P0THQRBKFPF';
const KEY = 'store-id-key-for-one-customer';

const env = (db, over = {}) => ({
  DB: db,
  MS_TENANT_ID: 'tenant', MS_CLIENT_ID: 'client', MS_CLIENT_SECRET: 'secret',
  MS_PRODUCT_ID: PRODUCT,
  ...over,
});

/** Azure and the collections API, both faked at the fetch boundary. */
function mockMicrosoft({ token = 'a-token', collections, tokenStatus = 200,
                         collectionsStatus = 200 } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const href = String(url);
    calls.push({ url: href, init });
    if (href.includes('login.microsoftonline.com')) {
      return new Response(
        JSON.stringify(tokenStatus === 200
          ? { access_token: token }
          : { error_description: 'AADSTS7000215: bad secret' }),
        { status: tokenStatus });
    }
    return new Response(JSON.stringify(collections || { items: [] }),
                        { status: collectionsStatus });
  };
  return calls;
}

const post = async (db, body, over = {}) => {
  const res = await worker.fetch(new Request('https://w.example/v1/microsoft', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }), env(db, over), {});
  return { status: res.status, out: await res.json() };
};

const getTicket = async (db, over = {}) => {
  const res = await worker.fetch(
    new Request('https://w.example/v1/microsoft/ticket'), env(db, over), {});
  return { status: res.status, out: await res.json() };
};

const item = (status = 'Active', endDate = '2027-01-01T00:00:00Z') => ({
  productId: PRODUCT, status, endDate,
  beneficiary: { identityValue: 'customer-one' },
});

console.log('the service ticket');

await test('a ticket is minted for the collections key audience', async () => {
  const db = makeD1();
  const calls = mockMicrosoft();
  const { status, out } = await getTicket(db);
  assert.strictEqual(status, 200);
  assert.strictEqual(out.ticket, 'a-token');
  const body = String(calls[0].init.body);
  assert.ok(body.includes('b2b%2Fkeys%2Fcreate%2Fcollections'),
    'the ticket audience is not the query audience; the wrong one is valid and refused');
});

await test('a bad secret is 502, never a refusal', async () => {
  const db = makeD1();
  mockMicrosoft({ tokenStatus: 401 });
  const { status, out } = await getTicket(db);
  assert.strictEqual(status, 502, 'the app reads 5xx as could-not-ask, 403 as did-not-pay');
  assert.match(out.message, /AADSTS7000215/, 'Azure names the cause and it is worth keeping');
});

console.log('exchanging a key');

await test('an active subscription becomes a licence', async () => {
  const db = makeD1();
  mockMicrosoft({ collections: { items: [item()] } });
  const { status, out } = await post(db, { collectionsKey: KEY });
  assert.strictEqual(status, 200);
  assert.ok(out.licence_key);
  const row = db.query('SELECT status FROM licences')[0];
  assert.strictEqual(row.status, 'active');
});

await test('one customer maps to one licence however often they ask', async () => {
  const db = makeD1();
  mockMicrosoft({ collections: { items: [item()] } });
  const first = await post(db, { collectionsKey: KEY });
  const second = await post(db, { collectionsKey: KEY });
  assert.strictEqual(first.out.licence_key, second.out.licence_key);
  assert.strictEqual(db.query('SELECT COUNT(*) AS n FROM licences')[0].n, 1);
});

await test('a lapsed subscription expires ITS licence and nothing else', async () => {
  const db = makeD1();
  db.sqlite.prepare(`INSERT INTO licences (licence_key, tier, status, plan)
                     VALUES ('DAWN-PADDLE-CUSTOMER', 'managed', 'active', 'standard')`).run();
  mockMicrosoft({ collections: { items: [item()] } });
  const { out } = await post(db, { collectionsKey: KEY });

  mockMicrosoft({ collections: { items: [item('Expired')] } });
  const { status } = await post(db, { collectionsKey: KEY });
  assert.strictEqual(status, 403);

  const rows = db.query('SELECT licence_key, status FROM licences');
  const mine = rows.find((r) => r.licence_key === out.licence_key);
  const other = rows.find((r) => r.licence_key === 'DAWN-PADDLE-CUSTOMER');
  assert.strictEqual(mine.status, 'expired');
  assert.strictEqual(other.status, 'active',
    'a lapsed Store subscription must not switch off something bought elsewhere');
});

await test('owning nothing is a refusal, not an error', async () => {
  const db = makeD1();
  mockMicrosoft({ collections: { items: [] } });
  const { status, out } = await post(db, { collectionsKey: KEY });
  assert.strictEqual(status, 403);
  assert.strictEqual(out.error, 'not_subscribed');
  assert.strictEqual(db.query('SELECT COUNT(*) AS n FROM licences')[0].n, 0);
});

await test('a resubscription is not refused on the stale row', async () => {
  /* A customer who cancelled and came back has BOTH rows. Refusing on the
     expired one would lock out somebody who has just paid again. */
  const db = makeD1();
  mockMicrosoft({ collections: { items: [item('Expired', '2026-01-01T00:00:00Z'),
                                          item('Active')] } });
  const { status } = await post(db, { collectionsKey: KEY });
  assert.strictEqual(status, 200);
});

console.log('configuration and refusal, kept apart');

for (const name of ['MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_PRODUCT_ID']) {
  await test(`without ${name} it is 503 and Microsoft is not asked`, async () => {
    const db = makeD1();
    const calls = mockMicrosoft({ collections: { items: [item()] } });
    const { status, out } = await post(db, { collectionsKey: KEY }, { [name]: undefined });
    assert.strictEqual(status, 503);
    assert.strictEqual(out.error, 'microsoft_not_configured');
    assert.strictEqual(calls.length, 0, 'nothing is asked when we know we cannot ask');
  });
}

await test('a key Microsoft rejects is a bad key, not an outage', async () => {
  /* MEASURED against the live API on 2026-09-12: a junk key comes back 400,
     because Microsoft authenticated the request and then looked at the key.
     Reported as 'unreachable' it sends somebody to check Cloudflare for a
     fault in the client, and the app sits on grace waiting for a network that
     was never down. */
  const db = makeD1();
  mockMicrosoft({ collections: {}, collectionsStatus: 400 });
  const { status, out } = await post(db, { collectionsKey: 'not-a-real-key' });
  assert.strictEqual(status, 400, 'a client fault must not read as 5xx');
  assert.strictEqual(out.error, 'bad_collections_key');
  assert.match(out.message, /ticket/, 'say how to get a good one');
});

await test('a 500 from Microsoft IS an outage, and still reads as one', async () => {
  /* The positive control for the test above: narrowing 400 must not swallow
     the case the old branch existed for. */
  const db = makeD1();
  mockMicrosoft({ collections: {}, collectionsStatus: 500 });
  const { status, out } = await post(db, { collectionsKey: 'a-key' });
  assert.strictEqual(status, 502);
  assert.strictEqual(out.error, 'microsoft_unreachable');
});

await test('a refused query names Partner Center rather than the customer', async () => {
  /* THE failure worth naming. A token that mints and a query that is refused
     almost always means the registration was never added in Partner Center.
     Reported as "owns nothing", it sends somebody to look at the app. */
  const db = makeD1();
  mockMicrosoft({ collections: {}, collectionsStatus: 403 });
  const { status, out } = await post(db, { collectionsKey: KEY });
  assert.strictEqual(status, 502);
  assert.strictEqual(out.error, 'microsoft_auth_failed');
  assert.match(out.message, /Partner Center/);
});

await test('no key is a bad request, and asks nothing of Microsoft', async () => {
  const db = makeD1();
  const calls = mockMicrosoft();
  const { status } = await post(db, {});
  assert.strictEqual(status, 400);
  assert.strictEqual(calls.length, 0);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exitCode = 1;
