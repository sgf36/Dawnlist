/**
 * The Paddle-Signature header, as Paddle actually sends it.
 * Run: node test/signature.test.mjs
 *
 * What these protect, in one line each:
 *   - during a secret rotation there are several h1 values, and any may match;
 *   - a refused delivery tells the caller nothing about why, and the log all.
 */
import assert from 'node:assert';
import { handlePaddleWebhook, verifySignature } from '../src/paddle.js';
import { makeD1 } from './d1.mjs';
import { SECRET, hmac, sign } from './paddle-sign.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const body = '{"event_id":"evt_sig","event_type":"customer.updated","data":{}}';
const ts = () => Math.floor(Date.now() / 1000);

console.log('several h1 values');

await test('the matching h1 verifies when it comes FIRST', async () => {
  const t = ts();
  const header = `ts=${t};h1=${await hmac(body, SECRET, t)};h1=${await hmac(body, 'retired-secret', t)}`;
  assert.equal((await verifySignature(body, header, SECRET)).ok, true);
});

await test('the matching h1 verifies when it comes LAST', async () => {
  const t = ts();
  const header = `ts=${t};h1=${await hmac(body, 'retired-secret', t)};h1=${await hmac(body, SECRET, t)}`;
  assert.equal((await verifySignature(body, header, SECRET)).ok, true);
});

await test('negative control: several h1 values, none matching, is refused', async () => {
  const t = ts();
  const header = `ts=${t};h1=${await hmac(body, 'one', t)};h1=${await hmac(body, 'two', t)}`;
  const r = await verifySignature(body, header, SECRET);
  assert.equal(r.ok, false);
  assert.match(r.reason, /wrong secret/);
});

await test('an empty h1 is not a candidate, so it cannot be matched', async () => {
  assert.equal((await verifySignature(body, `ts=${ts()};h1=`, SECRET)).reason,
    'malformed Paddle-Signature');
});

console.log('\nthe 401');

async function capture(fn) {
  const lines = [];
  const real = console.warn;
  console.warn = (...args) => lines.push(JSON.stringify(args));
  try { return { result: await fn(), lines }; } finally { console.warn = real; }
}

await test('a refused delivery says bad_signature and nothing else', async () => {
  const old = ts() - 3600;
  const { result: res, lines } = await capture(() => handlePaddleWebhook(
    new Request('https://w/paddle/webhook', {
      method: 'POST', body, headers: { 'Paddle-Signature': `ts=${old};h1=00` },
    }), { DB: makeD1(), PADDLE_WEBHOOK_SECRET: SECRET }));

  assert.equal(res.status, 401);
  assert.deepEqual(await res.json(), { error: 'bad_signature' },
    'the caller is not told whether the timestamp or the signature failed');
  assert.ok(lines.some((l) => l.includes('timestamp')),
    'but the log says which, so a stale replay is not mistaken for a wrong secret');
});

await test('positive control: a correctly signed delivery is not refused', async () => {
  const res = await handlePaddleWebhook(new Request('https://w/paddle/webhook', {
    method: 'POST', body, headers: { 'Paddle-Signature': await sign(body) },
  }), { DB: makeD1(), PADDLE_WEBHOOK_SECRET: SECRET });
  assert.equal(res.status, 200);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
