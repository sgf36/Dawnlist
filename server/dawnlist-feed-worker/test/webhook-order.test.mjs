/**
 * Paddle events applied in the order they HAPPENED, not the order they arrived.
 * Run: node test/webhook-order.test.mjs
 *
 * What these protect, in one line each:
 *   - an older event delivered late never undoes a newer one;
 *   - a grant delivered after a newer cancellation issues no working licence;
 *   - status comes from the payload's data.status, and past_due keeps access;
 *   - subscription.resumed restores access;
 *   - a licence support suspended stays suspended whatever Paddle sends.
 */
import assert from 'node:assert';
import { makeD1 } from './d1.mjs';
import { deliver } from './paddle-sign.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const STANDARD = 'pri_standard_test';
const GLOBAL = 'pri_global_test';

function stubPaddleAndResend() {
  const emails = [];
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    if (u.includes('/customers/')) {
      return new Response(JSON.stringify({ data: { email: 'buyer@example.test', locale: 'en' } }));
    }
    if (u.includes('api.resend.com/emails')) {
      emails.push(JSON.parse(init.body));
      return new Response(JSON.stringify({ id: `re_${emails.length}` }));
    }
    return new Response('{}', { status: 404 });
  };
  return emails;
}

function setup() {
  const db = makeD1();
  const emails = stubPaddleAndResend();
  const env = { DB: db, PADDLE_PRICE_STANDARD: STANDARD, PADDLE_PRICE_GLOBAL: GLOBAL,
                RESEND_API_KEY: 're_test', PADDLE_API_KEY: 'pdl_test' };
  const send = async (event) => (await deliver(env, event)).out;
  return { db, emails, send };
}

let seq = 0;
const at = (hour) => `2026-09-11T${hour}:00:00.000000Z`;
function ev(type, { sub = 'sub_1', time, status, price = STANDARD } = {}) {
  const data = { id: sub, customer_id: 'ctm_1',
                 items: [{ price: { id: price, billing_cycle: { interval: 'month' } } }] };
  if (status) data.status = status;
  return { event_id: `evt_${++seq}`, event_type: type, occurred_at: time, data };
}
const licence = (db) => db.query('SELECT status, plan, max_postings_per_day FROM licences')[0];
const state = (db) => db.query('SELECT paddle_status, licence_status FROM paddle_subscriptions')[0];

console.log('out of order');

await test('an older cancellation delivered after a newer resume does not switch the licence off', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  await send(ev('subscription.resumed', { time: at('12'), status: 'active' }));
  const late = await send(ev('subscription.canceled', { time: at('11'), status: 'canceled' }));
  assert.equal(late.action, 'stale_ignored');
  assert.equal(licence(db).status, 'active');
});

await test('positive control: the same cancellation, in time order, does switch it off', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  await send(ev('subscription.canceled', { time: at('11'), status: 'canceled' }));
  assert.equal(licence(db).status, 'expired');
});

await test('a grant delivered after a newer cancellation issues no working licence, and no email', async () => {
  const { db, emails, send } = setup();
  const cancel = await send(ev('subscription.canceled', { time: at('11'), status: 'canceled' }));
  assert.equal(cancel.action, 'status_no_licence');
  const grant = await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  assert.equal(grant.action, 'issued_inactive');
  assert.equal(licence(db).status, 'expired');
  assert.equal(emails.length, 0, 'a key that does not work was not sent');
});

await test('positive control: a grant with nothing newer recorded is active and emailed', async () => {
  const { db, emails, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  assert.equal(licence(db).status, 'active');
  assert.equal(emails.length, 1);
});

await test('an older plan change delivered late does not undo a newer one', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  assert.equal((await send(ev('subscription.updated',
    { time: at('12'), status: 'active', price: GLOBAL }))).action, 'plan_changed');
  const late = await send(ev('subscription.updated', { time: at('10'), status: 'active' }));
  assert.equal(late.action, 'stale_ignored');
  assert.equal(licence(db).plan, 'global');
  assert.equal(licence(db).max_postings_per_day, 2500);
});

await test('events with no occurred_at still apply, in the order they arrive', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', {}));
  await send(ev('subscription.canceled', {}));
  assert.equal(licence(db).status, 'expired');
  await send(ev('subscription.resumed', {}));
  assert.equal(licence(db).status, 'active');
});

console.log('\nstatus from the payload');

await test('subscription.updated carries the status: paused and canceled end access, active and trialing restore it', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  const steps = [['10', 'paused', 'expired'], ['11', 'active', 'active'],
                 ['12', 'canceled', 'expired'], ['13', 'trialing', 'active']];
  for (const [hour, status, expected] of steps) {
    await send(ev('subscription.updated', { time: at(hour), status }));
    assert.equal(licence(db).status, expected, `after status ${status}`);
  }
});

await test('past_due keeps access, and is recorded until the payment goes through', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  await send(ev('subscription.past_due', { time: at('10'), status: 'past_due' }));
  assert.equal(licence(db).status, 'active', 'a retrying payment does not lock the customer out');
  assert.equal(state(db).paddle_status, 'past_due', 'but support can see it');
  await send(ev('subscription.updated', { time: at('11'), status: 'active' }));
  assert.equal(state(db).paddle_status, 'active');
});

await test('subscription.resumed restores access after a pause', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  await send(ev('subscription.paused', { time: at('10'), status: 'paused' }));
  assert.equal(licence(db).status, 'expired');
  await send(ev('subscription.resumed', { time: at('11'), status: 'active' }));
  assert.equal(licence(db).status, 'active');
});

console.log('\nsupport\'s decision stands');

await test('a suspended licence is never reactivated by a webhook', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  db.sqlite.exec("UPDATE licences SET status = 'suspended'");
  await send(ev('subscription.activated', { time: at('10'), status: 'active' }));
  await send(ev('subscription.resumed', { time: at('11'), status: 'active' }));
  await send(ev('subscription.updated', { time: at('12'), status: 'active' }));
  await send({ event_id: `evt_${++seq}`, event_type: 'transaction.completed', occurred_at: at('13'),
               data: { id: 'txn_1', subscription_id: 'sub_1', customer_id: 'ctm_1',
                       items: [{ price: { id: STANDARD, billing_cycle: { interval: 'month' } } }] } });
  assert.equal(licence(db).status, 'suspended');
});

await test('positive control: an expired licence IS reactivated by the same kind of event', async () => {
  const { db, send } = setup();
  await send(ev('subscription.created', { time: at('09'), status: 'active' }));
  await send(ev('subscription.canceled', { time: at('10'), status: 'canceled' }));
  await send(ev('subscription.activated', { time: at('11'), status: 'active' }));
  assert.equal(licence(db).status, 'active');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
