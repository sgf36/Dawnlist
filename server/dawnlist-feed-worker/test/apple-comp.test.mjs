/**
 * Comp access for Mac users who never paid — and the gates that keep everyone
 * else out. Run: node test/apple-comp.test.mjs
 *
 * AGAINST A REAL SQLITE ENGINE, deliberately. `apple.test.mjs` uses a
 * hand-rolled stub that pattern-matches SQL, and while building this the stub
 * answered `null` to a SELECT that had merely gained a column — so a refusal
 * silently stopped switching a licence off. A comp gate is exactly the kind of
 * thing that must not be proved by a fake that agrees with its author.
 *
 * What each protects, in one line:
 *   - a subscription that began with a comp offer AND was comped survives
 *     Apple saying it lapsed;
 *   - one that was PAID for never can, whatever the flag says;
 *   - a comp offer that nobody comped never can either;
 *   - a revoked licence beats comp, and an Apple check cannot revive it.
 */
import assert from 'node:assert';
import worker from '../src/index.js';
import { makeD1 } from './d1.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const ADMIN = 'DAWN-ADMINAAA-BBBBCCCC';
const COMP_OFFER = 'Dawnlist Comps';
const TXN = '2000000111';

function seed(db) {
  db.sqlite.prepare(`INSERT INTO licences (licence_key, tier, status, plan)
                     VALUES (?, 'managed', 'active', 'owner')`).run(ADMIN);
  db.sqlite.prepare("INSERT INTO licence_roles (licence_key, role) VALUES (?, 'admin')").run(ADMIN);
}

/** A settled Apple purchase, as `/v1/apple` would have left it. */
function purchase(db, { offer = COMP_OFFER, comp = 0, status = 'active',
                        licence = 'DAWN-MACUSER-0001' } = {}) {
  db.sqlite.prepare(
    `INSERT INTO licences (licence_key, tier, status, plan)
     VALUES (?, 'managed', ?, 'standard')`).run(licence, status);
  db.sqlite.prepare(
    `INSERT INTO apple_transactions (original_transaction_id, licence_key,
       environment, status, expires_at, created_at, updated_at,
       offer_identifier, comp)
     VALUES (?, ?, 'Production', 'active', NULL, datetime('now'),
             datetime('now'), ?, ?)`).run(TXN, licence, offer, comp);
  return licence;
}

const env = (db) => ({ DB: db, APPLE_COMP_OFFERS: COMP_OFFER });

const admin = async (db, method, path, body) => {
  const res = await worker.fetch(new Request(`https://w.example${path}`, {
    method,
    headers: { 'cf-connecting-ip': '192.0.2.10', 'content-type': 'application/json',
               authorization: `Bearer ${ADMIN}` },
    body: body ? JSON.stringify(body) : undefined,
  }), env(db), {});
  return { status: res.status, out: await res.json() };
};

const licenceStatus = (db, key) =>
  db.query('SELECT licence_key, status, expires_at FROM licences')
    .find((r) => r.licence_key === key);

console.log('who may be comped at all');

await test('a subscription that began with a comp offer may be comped', async () => {
  const db = makeD1(); seed(db); purchase(db);
  const { status, out } = await admin(db, 'POST', '/admin/apple-comp',
    { original_transaction_id: TXN, comp: true });
  assert.strictEqual(status, 200);
  assert.strictEqual(out.comp, true);
  assert.strictEqual(db.query('SELECT comp FROM apple_transactions')[0].comp, 1);
});

await test('a subscription somebody PAID for can never be comped', async () => {
  const db = makeD1(); seed(db);
  // A purchase carries no offer identifier at all. This is the gate that does
  // not depend on anyone remembering a rule.
  purchase(db, { offer: null });
  const { status, out } = await admin(db, 'POST', '/admin/apple-comp',
    { original_transaction_id: TXN, comp: true });
  assert.strictEqual(status, 409);
  assert.strictEqual(out.error, 'not_comp_eligible');
  assert.strictEqual(db.query('SELECT comp FROM apple_transactions')[0].comp, 0);
});

await test('an offer that is not on the comp list is refused, and named', async () => {
  const db = makeD1(); seed(db); purchase(db, { offer: 'Launch Promo' });
  const { status, out } = await admin(db, 'POST', '/admin/apple-comp',
    { original_transaction_id: TXN, comp: true });
  assert.strictEqual(status, 409);
  assert.strictEqual(out.began_with, 'Launch Promo',
    'naming the offer it did begin with is what makes the refusal actionable');
});

await test('comp can always be switched OFF, whatever the offer was', async () => {
  const db = makeD1(); seed(db); purchase(db, { offer: null, comp: 1 });
  const { status } = await admin(db, 'POST', '/admin/apple-comp',
    { original_transaction_id: TXN, comp: false });
  assert.strictEqual(status, 200, 'withdrawing access must never be blocked by a gate');
  assert.strictEqual(db.query('SELECT comp FROM apple_transactions')[0].comp, 0);
});

console.log('what happens when Apple stops saying yes');

/** `/v1/apple` with Apple reporting the subscription expired. */
const lapsed = async (db) => {
  // `settle` is called directly rather than through the route, because what is
  // under test is the DECISION, and reaching it through the route would mean
  // standing up Apple's key material and a signed JWS to ask a question that
  // has nothing to do with either.
  const { settle } = await import('../src/apple.js');
  return settle(env(db), {
    originalTransactionId: TXN, entitled: false, status: 'expired',
    expiresAt: '2026-01-01T00:00:00Z', environment: 'Production',
  });
};

await test('a comped guest keeps the app, with no expiry', async () => {
  const db = makeD1(); seed(db);
  const licence = purchase(db, { comp: 1 });
  const res = await lapsed(db);
  const body = await res.json();
  assert.strictEqual(res.status, 200);
  assert.strictEqual(body.status, 'comp');
  const row = licenceStatus(db, licence);
  assert.strictEqual(row.status, 'active');
  assert.strictEqual(row.expires_at, null, 'a comp with an expiry is not a comp');
});

await test('a paying customer who lapsed loses it, flag or no flag', async () => {
  const db = makeD1(); seed(db);
  // comp = 1 on a PURCHASE. The flag is set and the evidence is missing, so
  // the gate must still refuse: this is the failure that would matter.
  const licence = purchase(db, { offer: null, comp: 1 });
  const res = await lapsed(db);
  assert.strictEqual(res.status, 403);
  assert.strictEqual(licenceStatus(db, licence).status, 'expired');
});

await test('a comp offer that was never comped lapses like anyone else', async () => {
  const db = makeD1(); seed(db);
  const licence = purchase(db, { comp: 0 });
  const res = await lapsed(db);
  assert.strictEqual(res.status, 403);
  assert.strictEqual(licenceStatus(db, licence).status, 'expired');
});

await test('a REFUNDED licence beats comp', async () => {
  /* Apple gave the customer their money back. Comp must not quietly hand the
     product back to somebody who asked to be released from it.
     Note: 'revoked' is not a licence status at all - the schema allows
     active, expired, refunded and suspended - so a guard written against it
     would never fire. One was, and this test is why it did not ship. */
  const db = makeD1(); seed(db);
  const licence = purchase(db, { comp: 1, status: 'refunded' });
  const res = await lapsed(db);
  assert.strictEqual(res.status, 403);
  // What matters is that comp did not REVIVE it. The ordinary refusal then
  // writes Apple's own verdict over the row, which is existing behaviour and
  // not this gate's business.
  assert.notStrictEqual(licenceStatus(db, licence).status, 'active');
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exitCode = 1;
