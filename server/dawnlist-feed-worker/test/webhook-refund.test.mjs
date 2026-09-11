/**
 * A refund or a chargeback ends the licence it paid for.
 * Run: node test/webhook-refund.test.mjs
 *
 * What these protect, in one line each:
 *   - an approved FULL refund, or any chargeback, sets the licence 'refunded';
 *   - a partial refund, or one still awaiting approval, changes nothing;
 *   - the licence is found through the transaction the refund names;
 *   - nothing Paddle sends afterwards reactivates a refunded licence;
 *   - a refunded licence is refused by the feed.
 */
import assert from 'node:assert';
import worker from '../src/index.js';
import { makeD1 } from './d1.mjs';
import { deliver } from './paddle-sign.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const PRICE = 'pri_standard_test';
const monthly = [{ price: { id: PRICE, billing_cycle: { interval: 'month' } } }];

let seq = 0;
const at = (hour) => `2026-09-11T${hour}:00:00.000000Z`;

function setup() {
  const db = makeD1();
  // No customer email is found, so nothing is sent; delivery is not under test.
  globalThis.fetch = async () => new Response('{}', { status: 404 });
  const env = { DB: db, PADDLE_PRICE_STANDARD: PRICE, PADDLE_API_KEY: 'pdl_test' };
  const send = async (event) => (await deliver(env, { event_id: `evt_${++seq}`, ...event })).out;
  return { db, env, send };
}

/** A subscription bought and paid: subscription.created then its first transaction. */
async function buy(send, { sub = 'sub_1', txn = 'txn_1' } = {}) {
  await send({ event_type: 'subscription.created', occurred_at: at('09'),
               data: { id: sub, status: 'active', customer_id: 'ctm_1', items: monthly } });
  await send({ event_type: 'transaction.completed', occurred_at: at('09'),
               data: { id: txn, subscription_id: sub, customer_id: 'ctm_1', items: monthly } });
}

const adjustment = (type, data) => ({ event_type: type, occurred_at: at('10'),
  data: { id: `adj_${seq}`, transaction_id: 'txn_1', ...data } });
const status = (db, sub = 'sub_1') =>
  db.query('SELECT status FROM licences WHERE paddle_subscription_id = ?', sub)[0]?.status;

const FULL = { action: 'refund', status: 'approved', type: 'full', items: [{ type: 'full' }] };

console.log('what ends access');

await test('an approved full refund ends access at once', async () => {
  const { db, send } = setup();
  await buy(send);
  const out = await send(adjustment('adjustment.created', FULL));
  assert.equal(out.action, 'refunded');
  assert.equal(status(db), 'refunded');
});

await test('a chargeback ends access, without waiting for approval', async () => {
  const { db, send } = setup();
  await buy(send);
  await send(adjustment('adjustment.created', { action: 'chargeback', status: 'pending_approval' }));
  assert.equal(status(db), 'refunded');
});

await test('a full refund marked only by its lines is still a full refund', async () => {
  const { db, send } = setup();
  await buy(send);
  await send(adjustment('adjustment.created',
    { action: 'refund', status: 'approved', items: [{ type: 'full' }, { type: 'full' }] }));
  assert.equal(status(db), 'refunded');
});

console.log('\nwhat does not');

await test('a partial refund changes nothing', async () => {
  const { db, send } = setup();
  await buy(send);
  const out = await send(adjustment('adjustment.created',
    { action: 'refund', status: 'approved', type: 'partial', items: [{ type: 'partial' }] }));
  assert.equal(out.action, 'adjustment_no_change');
  assert.equal(status(db), 'active');
});

await test('a refund awaiting approval changes nothing until adjustment.updated approves it', async () => {
  const { db, send } = setup();
  await buy(send);
  await send(adjustment('adjustment.created', { ...FULL, status: 'pending_approval' }));
  assert.equal(status(db), 'active', 'a refund that may yet be rejected has not cut anybody off');
  await send(adjustment('adjustment.updated', FULL));
  assert.equal(status(db), 'refunded');
});

await test('a rejected refund changes nothing', async () => {
  const { db, send } = setup();
  await buy(send);
  await send(adjustment('adjustment.updated', { ...FULL, status: 'rejected' }));
  assert.equal(status(db), 'active');
});

console.log('\nfinding the licence');

await test('the licence is found through the transaction, with no subscription id on the refund', async () => {
  const { db, send } = setup();
  await buy(send, { sub: 'sub_7', txn: 'txn_7' });
  await buy(send, { sub: 'sub_8', txn: 'txn_8' });
  await send(adjustment('adjustment.created', { ...FULL, transaction_id: 'txn_7' }));
  assert.equal(status(db, 'sub_7'), 'refunded');
  assert.equal(status(db, 'sub_8'), 'active', 'the other customer is untouched');
});

await test('an unseen transaction falls back to the subscription id the refund carries', async () => {
  const { db, send } = setup();
  await send({ event_type: 'subscription.created', occurred_at: at('09'),
               data: { id: 'sub_9', status: 'active', customer_id: 'ctm_1', items: monthly } });
  await send(adjustment('adjustment.created',
    { ...FULL, transaction_id: 'txn_never_seen', subscription_id: 'sub_9' }));
  assert.equal(status(db, 'sub_9'), 'refunded');
});

await test('a one-time purchase is found by its transaction id', async () => {
  const { db, send } = setup();
  await send({ event_type: 'transaction.completed', occurred_at: at('09'),
               data: { id: 'txn_once', customer_id: 'ctm_1', items: [{ price: { id: PRICE } }] } });
  await send(adjustment('adjustment.created', { ...FULL, transaction_id: 'txn_once' }));
  assert.equal(status(db, 'txn_once'), 'refunded');
});

await test('a refund that matches no licence changes nothing and says so', async () => {
  const { db, send } = setup();
  await buy(send);
  const out = await send(adjustment('adjustment.created', { ...FULL, transaction_id: 'txn_other' }));
  assert.equal(out.action, 'refund_no_licence');
  assert.equal(status(db), 'active');
});

console.log('\nafterwards');

await test('a newer "active" from Paddle does not reactivate a refunded licence', async () => {
  const { db, send } = setup();
  await buy(send);
  await send(adjustment('adjustment.created', FULL));
  await send({ event_type: 'subscription.updated', occurred_at: at('11'),
               data: { id: 'sub_1', status: 'active', items: monthly } });
  await send({ event_type: 'subscription.activated', occurred_at: at('12'),
               data: { id: 'sub_1', status: 'active', items: monthly } });
  assert.equal(status(db), 'refunded');
});

await test('a refunded licence is refused by the feed, and was accepted before', async () => {
  const { db, send } = setup();
  await buy(send);
  const key = db.query('SELECT licence_key FROM licences')[0].licence_key;
  const check = () => worker.fetch(new Request('https://w.example/v1/licence', {
    headers: { authorization: `Bearer ${key}` } }), { DB: db }, {});

  assert.equal((await check()).status, 200);
  await send(adjustment('adjustment.created', FULL));
  const res = await check();
  assert.equal(res.status, 403);
  assert.equal((await res.json()).error, 'licence_inactive');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
