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
 * Run: node test/trial.test.mjs
 */
import assert from 'node:assert';
import { handleRedeem, handleAdmin } from '../src/codes.js';
import { PLANS } from '../src/plans.js';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

/** D1 stub that records the licence cap columns, which is the whole point. */
function makeDB({ codes = [], roles = [], redemptions = [] } = {}) {
  const licences = [];
  return {
    codes, roles, redemptions, licences,
    prepare(sql) {
      return {
        _a: [],
        bind(...a) { this._a = a; return this; },
        async first() {
          if (sql.includes('FROM codes')) {
            return codes.find(c => c.code === this._a[0]) || null;
          }
          if (sql.includes('FROM licence_roles')) {
            return roles.find(r => r.licence_key === this._a[0]) || null;
          }
          if (sql.includes('FROM licences')) {
            return licences.find(l => l.licence_key === this._a[0]) || null;
          }
          if (sql.includes('COUNT')) return { n: redemptions.length };
          return null;
        },
        async all() {
          // handleRedeem reads ALL codes and matches on a normalised
          // comparison, so a code typed without hyphens still works.
          if (sql.includes('FROM codes')) return { results: codes };
          return { results: [] };
        },
        async run() {
          if (sql.includes('INSERT INTO licences')) {
            licences.push({
              licence_key: this._a[0], tier: this._a[1], status: 'active',
              plan: this._a[2],
              max_postings_per_day: this._a[3],
              max_refreshes_per_day: this._a[4],
              max_saved_queries: this._a[5],
            });
          } else if (sql.includes('INSERT INTO codes')) {
            codes.push({ code: this._a[0], note: this._a[1],
                         max_uses: this._a[2], revoked: 0,
                         role: this._a[4], plan: this._a[5] });
          } else if (sql.includes('INSERT INTO redemptions')) {
            redemptions.push({ code: this._a[0] });
          }
          return { success: true };
        },
      };
    },
  };
}

const newKey = () => 'DAWN-TEST-' + Math.random().toString(36).slice(2, 8).toUpperCase();

async function redeem(db, code) {
  const req = new Request('https://w/redeem', {
    method: 'POST', body: JSON.stringify({ code }),
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': '1.2.3.4' },
  });
  return (await handleRedeem(req, { DB: db }, newKey)).json();
}

console.log('redeeming');

await test('a TRIAL code grants the trial allowance, not the default', async () => {
  const db = makeDB({ codes: [{ code: 'TRY', role: 'managed', plan: 'trial',
                                max_uses: 1, revoked: 0 }] });
  const out = await redeem(db, 'TRY');
  assert.equal(out.ok, true);
  const lic = db.licences[0];
  assert.equal(lic.plan, 'trial');
  assert.equal(lic.max_postings_per_day, PLANS.trial.maxPostingsPerDay);
  assert.notEqual(lic.max_postings_per_day, PLANS.standard.maxPostingsPerDay,
    'a trial must not silently get the full allowance');
});

await test('a comp code can still grant the full plan', async () => {
  const db = makeDB({ codes: [{ code: 'COMP', role: 'managed', plan: 'standard',
                                max_uses: 1, revoked: 0 }] });
  await redeem(db, 'COMP');
  assert.equal(db.licences[0].max_postings_per_day, PLANS.standard.maxPostingsPerDay);
});

await test('a code predating plans falls back rather than granting nothing', async () => {
  // plan IS NULL on codes issued before migration 002.
  const db = makeDB({ codes: [{ code: 'OLD', role: 'managed', plan: null,
                                max_uses: 1, revoked: 0 }] });
  await redeem(db, 'OLD');
  assert.equal(db.licences[0].plan, 'standard');
  assert.ok(db.licences[0].max_postings_per_day > 0);
});

console.log('minting');

await test('minting defaults to TRIAL, the smallest allowance', async () => {
  // The safe default for a credential handed to a stranger.
  const db = makeDB({ roles: [{ licence_key: 'ADMIN', role: 'admin' }] });
  db.licences.push({ licence_key: 'ADMIN', status: 'active' });
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
