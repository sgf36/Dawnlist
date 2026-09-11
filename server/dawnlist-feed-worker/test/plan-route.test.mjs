/**
 * `/v1/plan` offers only plans somebody could actually buy here.
 * Run: node test/plan-route.test.mjs
 *
 * Global was flagged sellable while its Paddle price was deliberately unset,
 * so the app rendered "upgrade to Global" and no checkout could take the
 * money. Driven through the real `fetch`, because the ladder is only as good
 * as the environment the route hands it.
 */
import assert from 'node:assert';
import worker from '../src/index.js';
import { makeSearchDB } from './search-db.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

async function ladder(prices) {
  const res = await worker.fetch(new Request('https://w.example/v1/plan', {
    headers: { authorization: 'Bearer L1' },
  }), { DB: makeSearchDB({ plan: 'standard' }), ...prices }, {});
  assert.equal(res.status, 200);
  return (await res.json()).plans.map((p) => p.key);
}

console.log('the upgrade ladder');

await test('an unpriced Global is not offered', async () => {
  assert.deepEqual(await ladder({ PADDLE_PRICE_STANDARD: 'pri_standard' }), ['standard']);
});

await test('positive control: once Global is priced it IS offered', async () => {
  assert.deepEqual(await ladder({ PADDLE_PRICE_STANDARD: 'pri_standard',
                                  PADDLE_PRICE_GLOBAL: 'pri_global' }), ['standard', 'global']);
});

await test('with no prices at all nothing is offered, and the route still answers', async () => {
  assert.deepEqual(await ladder({}), []);
});

await test('a variable holding only commas and spaces is not a price', async () => {
  assert.deepEqual(await ladder({ PADDLE_PRICE_STANDARD: 'pri_standard',
                                  PADDLE_PRICE_GLOBAL: ' , ' }), ['standard']);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
