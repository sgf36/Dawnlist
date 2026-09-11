/**
 * Webhook logic, against a real SQLite D1. No network, no deploy.
 * Run: node test/paddle.test.mjs
 */
import assert from 'node:assert';
import { handlePaddleWebhook, newLicenceKey, verifySignature } from '../src/paddle.js';
import { makeD1 } from './d1.mjs';
import { SECRET, deliver, sign } from './paddle-sign.mjs';

let passed = 0, failed = 0;

async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const PRICE = 'pri_standard_test';
const ENV = { PADDLE_PRICE_STANDARD: PRICE };
const priced = (extra = {}) => ({ items: [{ price: { id: PRICE, ...extra } }] });
const licences = (db) => db.query('SELECT * FROM licences ORDER BY created_at, rowid');

function req(body, signature) {
  return new Request('https://x/paddle/webhook', {
    method: 'POST', body,
    headers: signature ? { 'Paddle-Signature': signature } : {},
  });
}

console.log('\nPaddle webhook');

await test('a valid signature verifies', async () => {
  const body = '{"a":1}';
  const r = await verifySignature(body, await sign(body), SECRET);
  assert.equal(r.ok, true);
});

await test('a wrong secret is rejected, and says so specifically', async () => {
  const body = '{"a":1}';
  const r = await verifySignature(body, await sign(body, 'other-secret'), SECRET);
  assert.equal(r.ok, false);
  assert.match(r.reason, /wrong secret/);
});

await test('a stale timestamp names the TIMESTAMP, not the secret', async () => {
  // "invalid signature" for a stale replay sends you hunting the wrong fault.
  const body = '{"a":1}';
  const old = Math.floor(Date.now() / 1000) - 3600;
  const r = await verifySignature(body, await sign(body, SECRET, old), SECRET);
  assert.equal(r.ok, false);
  assert.match(r.reason, /timestamp/);
});

await test('a missing secret is named, not treated as a mismatch', async () => {
  const r = await verifySignature('{}', 'ts=1;h1=abc', undefined);
  assert.match(r.reason, /not set/);
});

await test('a tampered body fails even with a valid-looking signature', async () => {
  const signature = await sign('{"amount":100}');
  const r = await verifySignature('{"amount":99999}', signature, SECRET);
  assert.equal(r.ok, false);
});

await test('an unsigned request is refused with 401', async () => {
  const env = { DB: makeD1(), PADDLE_WEBHOOK_SECRET: SECRET };
  const res = await handlePaddleWebhook(req('{}', null), env);
  assert.equal(res.status, 401);
});

await test('a completed transaction issues exactly one licence', async () => {
  const db = makeD1();
  const { status } = await deliver({ ...ENV, DB: db }, {
    event_id: 'evt_1', event_type: 'transaction.completed',
    data: { id: 'txn_1', ...priced() },
  });
  assert.equal(status, 200);
  assert.equal(licences(db).length, 1);
  assert.equal(licences(db)[0].tier, 'byo', 'a one-time purchase is the BYO tier');
});

await test('a subscription issues a MANAGED licence', async () => {
  const db = makeD1();
  await deliver({ ...ENV, DB: db }, {
    event_id: 'evt_2', event_type: 'subscription.created',
    data: { id: 'sub_1', ...priced() },
  });
  assert.equal(licences(db)[0].tier, 'managed');
});

await test('a retried delivery does NOT issue a second licence', async () => {
  // Worse than a missed one: the customer holds two keys, the meter splits
  // across them, and nothing looks wrong from either side.
  const db = makeD1();
  const env = { ...ENV, DB: db };
  const event = { event_id: 'evt_3', event_type: 'subscription.created',
                  data: { id: 'sub_2', ...priced() } };
  await deliver(env, event);
  const second = await deliver(env, event);
  assert.equal(second.status, 200);
  assert.equal(licences(db).length, 1);
  assert.equal(second.out.deduplicated, true);
});

await test('reactivating an existing subscription reuses its key', async () => {
  const db = makeD1();
  const env = { ...ENV, DB: db };
  await deliver(env, { event_id: 'e1', event_type: 'subscription.created',
                       data: { id: 'sub_9', ...priced() } });
  const key = licences(db)[0].licence_key;

  await deliver(env, { event_id: 'e2', event_type: 'subscription.activated',
                       data: { id: 'sub_9', ...priced() } });
  assert.equal(licences(db).length, 1);
  assert.equal(licences(db)[0].licence_key, key);
});

await test('a cancellation expires the licence', async () => {
  const db = makeD1();
  const env = { ...ENV, DB: db };
  await deliver(env, { event_id: 'c1', event_type: 'subscription.created',
                       data: { id: 'sub_3', ...priced() } });
  await deliver(env, { event_id: 'c2', event_type: 'subscription.canceled',
                       data: { id: 'sub_3' } });
  assert.equal(licences(db)[0].status, 'expired');
});

await test('an unrelated event is acknowledged, not errored', async () => {
  const db = makeD1();
  const { status } = await deliver({ ...ENV, DB: db },
    { event_id: 'x1', event_type: 'customer.updated', data: {} });
  assert.equal(status, 200);
  assert.equal(licences(db).length, 0);
});

await test('the licence key is never returned to the caller', async () => {
  // The webhook response goes to Paddle, not the customer. Delivery is a
  // separate, deliberate step.
  const db = makeD1();
  const { out } = await deliver({ ...ENV, DB: db }, {
    event_id: 'k1', event_type: 'subscription.created', data: { id: 'sub_4', ...priced() },
  });
  assert.ok(!JSON.stringify(out).includes(licences(db)[0].licence_key));
});

await test('licence keys are unique and shaped', async () => {
  const keys = new Set(Array.from({ length: 500 }, newLicenceKey));
  assert.equal(keys.size, 500);
  assert.match([...keys][0], /^DAWN(-[0-9A-Z]+)+$/);
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
