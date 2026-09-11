/**
 * One payment, one licence, one email — however Paddle delivers it.
 * Run: node test/webhook-once.test.mjs
 *
 * What these protect, in one line each:
 *   - two DIFFERENT events for one subscription, arriving together, issue once;
 *   - the SAME event delivered twice at once is processed once;
 *   - an event that failed part-way is not marked seen, so the retry works;
 *   - the database itself refuses a second licence for one subscription.
 *
 * Against a real SQLite D1 whose statements interleave the way D1's round
 * trips do; a stub would serialise the two deliveries and hide the race.
 */
import assert from 'node:assert';
import { makeD1 } from './d1.mjs';
import { deliver } from './paddle-sign.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const PRICE = 'pri_standard_test';

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

const envFor = (DB) => ({
  DB, PADDLE_PRICE_STANDARD: PRICE, RESEND_API_KEY: 're_test', PADDLE_API_KEY: 'pdl_test',
});
const items = [{ price: { id: PRICE, billing_cycle: { interval: 'month' } } }];
const created = (sub, eventId) => ({ event_id: eventId, event_type: 'subscription.created',
  data: { id: sub, customer_id: 'ctm_1', items } });
const completed = (sub, txn, eventId) => ({ event_id: eventId, event_type: 'transaction.completed',
  data: { id: txn, subscription_id: sub, customer_id: 'ctm_1', items } });
const licences = (db) => db.query('SELECT licence_key, paddle_subscription_id FROM licences');

/** A D1 whose first statement matching `pattern` fails, as a D1 outage would. */
function failingOnce(d1, pattern) {
  let armed = true;
  const trip = (sql) => {
    if (armed && pattern.test(sql)) { armed = false; throw new Error('D1_ERROR: simulated outage'); }
  };
  const wrap = (stmt) => ({
    ...stmt,
    bind: (...args) => wrap(stmt.bind(...args)),
    first: async (col) => { trip(stmt.sql); return stmt.first(col); },
    run: async () => { trip(stmt.sql); return stmt.run(); },
    all: async () => { trip(stmt.sql); return stmt.all(); },
  });
  return {
    ...d1,
    prepare: (sql) => wrap(d1.prepare(sql)),
    batch: async (stmts) => { stmts.forEach((s) => trip(s.sql)); return d1.batch(stmts); },
  };
}

console.log('one payment, one licence');

await test('two different events for one subscription, together, issue one licence and one email', async () => {
  const db = makeD1();
  const emails = stubPaddleAndResend();
  const env = envFor(db);
  const results = await Promise.all([
    deliver(env, created('sub_1', 'evt_created')),
    deliver(env, completed('sub_1', 'txn_1', 'evt_completed')),
  ]);
  assert.deepEqual(results.map((r) => r.status), [200, 200]);
  assert.equal(licences(db).length, 1, 'one key for one payment');
  assert.equal(emails.length, 1, 'and one email carrying it');
});

await test('the same event delivered twice at once is processed once', async () => {
  const db = makeD1();
  const emails = stubPaddleAndResend();
  const env = envFor(db);
  const results = await Promise.all([
    deliver(env, created('sub_2', 'evt_twice')),
    deliver(env, created('sub_2', 'evt_twice')),
  ]);
  assert.equal(licences(db).length, 1);
  assert.equal(emails.length, 1);
  assert.equal(results.filter((r) => r.out.deduplicated).length, 1,
    'the second delivery is answered as a duplicate');
});

await test('positive control: two different subscriptions issue two licences and two emails', async () => {
  const db = makeD1();
  const emails = stubPaddleAndResend();
  const env = envFor(db);
  await Promise.all([
    deliver(env, created('sub_a', 'evt_a')),
    deliver(env, created('sub_b', 'evt_b')),
  ]);
  assert.equal(licences(db).length, 2);
  assert.equal(emails.length, 2);
});

console.log('\nfailure part-way');

await test('an event that failed part-way is not marked seen, so Paddle\'s retry issues', async () => {
  const db = makeD1();
  stubPaddleAndResend();
  const flaky = failingOnce(db, /INSERT INTO licences/);
  await assert.rejects(deliver(envFor(flaky), created('sub_3', 'evt_retry')),
    'a failure must reach Paddle as a non-2xx so it retries');
  assert.deepEqual(db.query('SELECT event_id FROM webhook_events'), [],
    'the event is not recorded as handled');

  const retry = await deliver(envFor(flaky), created('sub_3', 'evt_retry'));
  assert.equal(retry.out.action, 'issued');
  assert.equal(licences(db).length, 1);
  assert.deepEqual(db.query('SELECT action FROM webhook_events'), [{ action: 'issued' }]);
});

console.log('\nthe database');

await test('a second licence for one subscription is refused by the schema', async () => {
  const db = makeD1();
  const insert = (key, sub) => db.sqlite.prepare(
    "INSERT INTO licences (licence_key, tier, paddle_subscription_id) VALUES (?, 'managed', ?)"
  ).run(key, sub);
  insert('K1', 'sub_x');
  assert.throws(() => insert('K2', 'sub_x'), /UNIQUE/);
});

await test('positive control: licences with no subscription are not constrained', async () => {
  const db = makeD1();
  db.sqlite.exec(`INSERT INTO licences (licence_key, tier) VALUES ('C1', 'byo'), ('C2', 'byo')`);
  assert.equal(db.query('SELECT COUNT(*) AS n FROM licences')[0].n, 2);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
