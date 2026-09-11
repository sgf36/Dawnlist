/**
 * Trial licences: the thing that makes the product tryable.
 *
 * PLANS.trial has existed since plans were written and there was no way to
 * issue one. `handleRedeem` minted a licence, set `tier`, and never touched
 * the cap columns — so every redeemed code, including one meant as a short
 * trial, got the Worker's full 700/day default. A trial was indistinguishable
 * from a paid subscription in the only place that decides what a licence may
 * actually do.
 *
 * Runs against a real SQLite D1, so the caps asserted are the ones written.
 *
 * Run: node test/trial.test.mjs
 */
import assert from 'node:assert';
import { handleRedeem, handleAdmin } from '../src/codes.js';
import { PLANS } from '../src/plans.js';
import { makeD1 } from './d1.mjs';
import { seedCode } from './seed.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const newKey = () => 'DAWN-TEST-' + Math.random().toString(36).slice(2, 8).toUpperCase();
const licences = (db) => db.query('SELECT * FROM licences');

async function redeem(db, code) {
  const req = new Request('https://w/redeem', {
    method: 'POST', body: JSON.stringify({ code }),
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': '1.2.3.4' },
  });
  return (await handleRedeem(req, { DB: db }, newKey)).json();
}

console.log('redeeming');

await test('a TRIAL code grants the trial allowance, not the default', async () => {
  const db = makeD1();
  seedCode(db, { code: 'TRY', role: 'managed', plan: 'trial' });
  const out = await redeem(db, 'TRY');
  assert.equal(out.ok, true);
  const lic = licences(db)[0];
  assert.equal(lic.plan, 'trial');
  assert.equal(lic.max_postings_per_day, PLANS.trial.maxPostingsPerDay);
  assert.notEqual(lic.max_postings_per_day, PLANS.standard.maxPostingsPerDay,
    'a trial must not silently get the full allowance');
});

await test('a comp code can still grant the full plan', async () => {
  const db = makeD1();
  seedCode(db, { code: 'COMP', role: 'managed', plan: 'standard' });
  await redeem(db, 'COMP');
  assert.equal(licences(db)[0].max_postings_per_day, PLANS.standard.maxPostingsPerDay);
});

await test('a code predating plans falls back rather than granting nothing', async () => {
  // plan IS NULL on codes issued before migration 002.
  const db = makeD1();
  seedCode(db, { code: 'OLD', role: 'managed', plan: null });
  await redeem(db, 'OLD');
  assert.equal(licences(db)[0].plan, 'standard');
  assert.ok(licences(db)[0].max_postings_per_day > 0);
});

console.log('minting');

await test('minting defaults to TRIAL, the smallest allowance', async () => {
  // The safe default for a credential handed to a stranger.
  const db = makeD1();
  db.sqlite.exec(`INSERT INTO licences (licence_key, tier, status) VALUES ('ADMIN', 'managed', 'active');
                  INSERT INTO licence_roles (licence_key, role) VALUES ('ADMIN', 'admin');`);
  const req = new Request('https://w/admin/codes', {
    method: 'POST',
    body: JSON.stringify({ note: 'for a tester' }),
    headers: { 'content-type': 'application/json', authorization: 'Bearer ADMIN' },
  });
  const out = await (await handleAdmin(req, { DB: db })).json();
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.equal(out.plan, 'trial');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
