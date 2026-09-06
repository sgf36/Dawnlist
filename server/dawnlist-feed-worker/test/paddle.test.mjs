/**
 * Webhook logic, against a stubbed D1. No network, no deploy.
 * Run: node test/paddle.test.mjs
 */
import assert from 'node:assert';
import { handlePaddleWebhook, newLicenceKey, verifySignature } from '../src/paddle.js';

const SECRET = 'pdl_ntfset_01test_secretvalue_for_local_checks_only';
let passed = 0, failed = 0;

async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

async function sign(body, secret = SECRET, ts = Math.floor(Date.now() / 1000)) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const mac = await crypto.subtle.sign('HMAC', key,
    new TextEncoder().encode(`${ts}:${body}`));
  const h1 = [...new Uint8Array(mac)].map(b => b.toString(16).padStart(2, '0')).join('');
  return `ts=${ts};h1=${h1}`;
}

/** Minimal D1 stub: enough to observe what the handler writes. */
function makeDB() {
  const licences = [], events = [];
  return {
    licences, events,
    prepare(sql) {
      return {
        _args: [],
        bind(...a) { this._args = a; return this; },
        async first() {
          if (sql.includes('FROM webhook_events')) {
            return events.find(e => e.event_id === this._args[0]) || null;
          }
          if (sql.includes('FROM licences')) {
            return licences.find(l => l.paddle_subscription_id === this._args[0]) || null;
          }
          return null;
        },
        async run() {
          if (sql.includes('INSERT INTO licences')) {
            licences.push({ licence_key: this._args[0], tier: this._args[1],
                            status: 'active', paddle_subscription_id: this._args[2] });
          } else if (sql.includes('UPDATE licences SET status')) {
            const target = this._args[0];
            for (const l of licences) {
              if (l.licence_key === target || l.paddle_subscription_id === target) {
                l.status = sql.includes('expired') ? 'expired' : 'active';
              }
            }
          } else if (sql.includes('INSERT INTO webhook_events')) {
            events.push({ event_id: this._args[0], event_type: this._args[1],
                          action: this._args[2] });
          }
          return {};
        },
      };
    },
  };
}

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
  const env = { DB: makeDB(), PADDLE_WEBHOOK_SECRET: SECRET };
  const res = await handlePaddleWebhook(req('{}', null), env);
  assert.equal(res.status, 401);
});

await test('a completed transaction issues exactly one licence', async () => {
  const db = makeDB();
  const body = JSON.stringify({
    event_id: 'evt_1', event_type: 'transaction.completed',
    data: { id: 'txn_1', items: [{ price: {} }] },
  });
  const res = await handlePaddleWebhook(req(body, await sign(body)),
    { DB: db, PADDLE_WEBHOOK_SECRET: SECRET });
  assert.equal(res.status, 200);
  assert.equal(db.licences.length, 1);
  assert.equal(db.licences[0].tier, 'byo', 'a one-time purchase is the BYO tier');
});

await test('a subscription issues a MANAGED licence', async () => {
  const db = makeDB();
  const body = JSON.stringify({
    event_id: 'evt_2', event_type: 'subscription.created',
    data: { id: 'sub_1' },
  });
  await handlePaddleWebhook(req(body, await sign(body)),
    { DB: db, PADDLE_WEBHOOK_SECRET: SECRET });
  assert.equal(db.licences[0].tier, 'managed');
});

await test('a retried delivery does NOT issue a second licence', async () => {
  // Worse than a missed one: the customer holds two keys, the meter splits
  // across them, and nothing looks wrong from either side.
  const db = makeDB();
  const env = { DB: db, PADDLE_WEBHOOK_SECRET: SECRET };
  const body = JSON.stringify({
    event_id: 'evt_3', event_type: 'subscription.created', data: { id: 'sub_2' },
  });
  const sig = await sign(body);
  await handlePaddleWebhook(req(body, sig), env);
  const second = await handlePaddleWebhook(req(body, sig), env);
  assert.equal(second.status, 200);
  assert.equal(db.licences.length, 1);
  assert.equal((await second.json()).deduplicated, true);
});

await test('reactivating an existing subscription reuses its key', async () => {
  const db = makeDB();
  const env = { DB: db, PADDLE_WEBHOOK_SECRET: SECRET };
  let body = JSON.stringify({ event_id: 'e1', event_type: 'subscription.created',
                              data: { id: 'sub_9' } });
  await handlePaddleWebhook(req(body, await sign(body)), env);
  const key = db.licences[0].licence_key;

  body = JSON.stringify({ event_id: 'e2', event_type: 'subscription.activated',
                          data: { id: 'sub_9' } });
  await handlePaddleWebhook(req(body, await sign(body)), env);
  assert.equal(db.licences.length, 1);
  assert.equal(db.licences[0].licence_key, key);
});

await test('a cancellation expires the licence', async () => {
  const db = makeDB();
  const env = { DB: db, PADDLE_WEBHOOK_SECRET: SECRET };
  let body = JSON.stringify({ event_id: 'c1', event_type: 'subscription.created',
                              data: { id: 'sub_3' } });
  await handlePaddleWebhook(req(body, await sign(body)), env);
  body = JSON.stringify({ event_id: 'c2', event_type: 'subscription.canceled',
                          data: { id: 'sub_3' } });
  await handlePaddleWebhook(req(body, await sign(body)), env);
  assert.equal(db.licences[0].status, 'expired');
});

await test('an unrelated event is acknowledged, not errored', async () => {
  const db = makeDB();
  const body = JSON.stringify({ event_id: 'x1', event_type: 'customer.updated', data: {} });
  const res = await handlePaddleWebhook(req(body, await sign(body)),
    { DB: db, PADDLE_WEBHOOK_SECRET: SECRET });
  assert.equal(res.status, 200);
  assert.equal(db.licences.length, 0);
});

await test('the licence key is never returned to the caller', async () => {
  // The webhook response goes to Paddle, not the customer. Delivery is a
  // separate, deliberate step.
  const db = makeDB();
  const body = JSON.stringify({ event_id: 'k1', event_type: 'subscription.created',
                                data: { id: 'sub_4' } });
  const res = await handlePaddleWebhook(req(body, await sign(body)),
    { DB: db, PADDLE_WEBHOOK_SECRET: SECRET });
  const text = await res.text();
  assert.ok(!text.includes(db.licences[0].licence_key));
});

await test('licence keys are unique and shaped', async () => {
  const keys = new Set(Array.from({ length: 500 }, newLicenceKey));
  assert.equal(keys.size, 500);
  assert.match([...keys][0], /^DAWN(-[0-9A-Z]+)+$/);
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
