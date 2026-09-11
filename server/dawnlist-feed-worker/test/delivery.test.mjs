/**
 * Whether the licence email went out is recorded on the licence, by code.
 * Run: node test/delivery.test.mjs
 *
 * What these protect, in one line each:
 *   - every issued licence ends in 'sent', 'failed' with a code, or 'skipped';
 *   - an exception during delivery is recorded, not thrown into the webhook;
 *   - a delivery still running shows as 'pending';
 *   - /admin/undelivered lists pending and failed Paddle licences, masked;
 *   - a successful resend takes a licence off that list.
 */
import assert from 'node:assert';
import worker from '../src/index.js';
import { makeD1 } from './d1.mjs';
import { deliver } from './paddle-sign.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  const quiet = { log: console.log, error: console.error, warn: console.warn };
  try {
    console.error = () => {}; console.warn = () => {};
    await fn();
    Object.assign(console, quiet);
    console.log(`  ok   ${name}`); passed++;
  } catch (e) {
    Object.assign(console, quiet);
    console.log(`  FAIL ${name}\n       ${e.message}`); failed++;
  }
}

const PRICE = 'pri_standard_test';
const items = [{ price: { id: PRICE, billing_cycle: { interval: 'month' } } }];
let seq = 0;
const created = (sub) => ({ event_id: `evt_${++seq}`, event_type: 'subscription.created',
  data: { id: sub, customer_id: 'ctm_1', items } });
const envFor = (DB) => ({ DB, PADDLE_PRICE_STANDARD: PRICE, PADDLE_API_KEY: 'pdl_test',
                          RESEND_API_KEY: 're_test' });
const rowFor = (db, sub) => db.query(
  `SELECT licence_key, delivery_status, delivery_error_code FROM licences
    WHERE paddle_subscription_id = ?`, sub)[0];

/**
 * Paddle's customer endpoint and Resend. `hold` keeps Resend from answering
 * until it resolves, so a test can look at a delivery that is still running.
 */
function stub({ customer = 200, customerRecord = null, resend = 200,
                resendBody = '{"id":"re_1"}', hold = null } = {}) {
  globalThis.fetch = async (url) => {
    if (String(url).includes('/customers/')) {
      if (customerRecord) return { ok: true, status: 200, json: async () => customerRecord };
      return customer === 200
        ? new Response(JSON.stringify({ data: { email: 'buyer@example.test', locale: 'en' } }))
        : new Response('{}', { status: customer });
    }
    if (hold) await hold;
    return new Response(resendBody, { status: resend });
  };
}

function seedAdmin(db) {
  db.sqlite.exec(`INSERT INTO licences (licence_key, tier, status, plan)
                  VALUES ('DAWN-ADMINKEY1', 'managed', 'active', 'owner')`);
  db.sqlite.exec("INSERT INTO licence_roles (licence_key, role) VALUES ('DAWN-ADMINKEY1', 'admin')");
  return 'DAWN-ADMINKEY1';
}

const admin = async (db, path, { key = 'DAWN-ADMINKEY1', body } = {}) => {
  const res = await worker.fetch(new Request(`https://w.example${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }), { ...envFor(db) }, {});
  return { status: res.status, out: await res.json() };
};

console.log('recorded on the licence');

await test('a delivered licence is recorded as sent', async () => {
  const db = makeD1();
  stub();
  await deliver(envFor(db), created('sub_ok'));
  assert.deepEqual(
    (({ delivery_status, delivery_error_code }) => ({ delivery_status, delivery_error_code }))(rowFor(db, 'sub_ok')),
    { delivery_status: 'sent', delivery_error_code: null });
});

await test('no address is recorded as failed, by cause', async () => {
  const db = makeD1();
  stub({ customer: 404 });
  await deliver(envFor(db), created('sub_noaddr'));
  const row = rowFor(db, 'sub_noaddr');
  assert.equal(row.delivery_status, 'failed');
  assert.equal(row.delivery_error_code, 'no_address_lookup_failed_404');
});

await test('a refused send is recorded as failed, by Resend\'s error name only', async () => {
  const db = makeD1();
  stub({ resend: 422, resendBody: JSON.stringify({ name: 'validation_error',
    message: 'buyer@example.test is not allowed' }) });
  await deliver(envFor(db), created('sub_refused'));
  const row = rowFor(db, 'sub_refused');
  assert.equal(row.delivery_status, 'failed');
  assert.equal(row.delivery_error_code, 'send_validation_error');
});

await test('an exception during delivery is recorded, and the webhook still answers 200', async () => {
  const db = makeD1();
  // A locale that throws when read: nothing in delivery expects it.
  stub({ customerRecord: { data: { email: 'buyer@example.test',
                                   locale: { toString() { throw new Error('boom'); } } } } });
  const { status, out } = await deliver(envFor(db), created('sub_throw'));
  assert.equal(status, 200);
  assert.equal(out.action, 'issued');
  const row = rowFor(db, 'sub_throw');
  assert.equal(row.delivery_status, 'failed');
  assert.equal(row.delivery_error_code, 'exception');
});

await test('a delivery still running shows as pending, then sent', async () => {
  const db = makeD1();
  let release;
  stub({ hold: new Promise((resolve) => { release = resolve; }) });
  const running = [];
  await deliver(envFor(db), created('sub_slow'), { ctx: { waitUntil: (p) => running.push(p) } });
  assert.equal(rowFor(db, 'sub_slow').delivery_status, 'pending');
  release();
  await Promise.all(running);
  assert.equal(rowFor(db, 'sub_slow').delivery_status, 'sent');
});

console.log('\n/admin/undelivered');

function seedDeliveries(db) {
  const insert = db.sqlite.prepare(
    `INSERT INTO licences (licence_key, tier, status, plan, paddle_subscription_id,
                           delivery_status, delivery_error_code)
     VALUES (?, 'managed', 'active', 'standard', ?, ?, ?)`);
  insert.run('DAWN-FAILED0001', 'sub_failed', 'failed', 'send_http_403');
  insert.run('DAWN-PENDING002', 'sub_pending', 'pending', null);
  insert.run('DAWN-SENTSENT03', 'sub_sent', 'sent', null);
  insert.run('DAWN-FROMCODE04', null, null, null);
}

await test('it lists pending and failed licences, and nothing else', async () => {
  const db = makeD1();
  seedAdmin(db);
  seedDeliveries(db);
  const { status, out } = await admin(db, '/admin/undelivered');
  assert.equal(status, 200);
  assert.deepEqual(out.licences.map((l) => l.paddle_subscription_id).sort(),
    ['sub_failed', 'sub_pending']);
  const failedRow = out.licences.find((l) => l.paddle_subscription_id === 'sub_failed');
  assert.equal(failedRow.delivery_error_code, 'send_http_403');
});

await test('it shows the last four characters of a key, never the key', async () => {
  const db = makeD1();
  seedAdmin(db);
  seedDeliveries(db);
  const { out } = await admin(db, '/admin/undelivered');
  const text = JSON.stringify(out);
  assert.ok(!text.includes('DAWN-FAILED0001') && !text.includes('DAWN-PENDING002'));
  assert.deepEqual(out.licences.map((l) => l.licence).sort(), ['…0001', '…G002']);
});

await test('an ordinary licence cannot read it', async () => {
  const db = makeD1();
  seedDeliveries(db);
  const { status } = await admin(db, '/admin/undelivered', { key: 'DAWN-FAILED0001' });
  assert.equal(status, 403);
});

await test('a successful resend takes the licence off the list', async () => {
  const db = makeD1();
  seedAdmin(db);
  seedDeliveries(db);
  stub();
  const sent = await admin(db, '/admin/resend',
    { body: { licence_key: 'DAWN-FAILED0001', to: 'buyer@example.test' } });
  assert.equal(sent.status, 200);
  const { out } = await admin(db, '/admin/undelivered');
  assert.deepEqual(out.licences.map((l) => l.paddle_subscription_id), ['sub_pending']);
});

await test('positive control: a failed resend leaves it on the list', async () => {
  const db = makeD1();
  seedAdmin(db);
  seedDeliveries(db);
  stub({ resend: 422, resendBody: '{"name":"validation_error"}' });
  const sent = await admin(db, '/admin/resend',
    { body: { licence_key: 'DAWN-FAILED0001', to: 'buyer@example.test' } });
  assert.equal(sent.status, 502);
  const { out } = await admin(db, '/admin/undelivered');
  assert.ok(out.licences.some((l) => l.paddle_subscription_id === 'sub_failed'));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
