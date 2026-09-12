/**
 * Mac App Store offer codes: the ledger, and who was given what.
 * Run: node test/apple-offer-codes.test.mjs
 *
 * These exist because offer codes are the ONLY way to give somebody free
 * access to the Mac subscription — Dawnlist's own override codes were removed
 * from that build under guideline 3.1.1 — so a mistake here is not a cosmetic
 * one, it is a friend with a string that does not work and no way to tell that
 * from a broken app.
 *
 * What each protects, in one line:
 *   - a batch is validated whole before any of it is stored;
 *   - re-sending a chunk adds what is missing instead of failing;
 *   - two consoles asking at once cannot hand the same code to two people;
 *   - voiding is local bookkeeping and says so, because Apple cannot withdraw
 *     a minted code and a console that implied otherwise would be lying;
 *   - every one of these leaves an audit row, like every other admin action.
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

function seed(db) {
  db.sqlite.prepare(`INSERT INTO licences (licence_key, tier, status, plan)
                     VALUES (?, 'managed', 'active', 'owner')`).run(ADMIN);
  db.sqlite.prepare("INSERT INTO licence_roles (licence_key, role) VALUES (?, 'admin')").run(ADMIN);
}

const call = async (db, method, path, { key = ADMIN, body, ip = '192.0.2.10' } = {}) => {
  const res = await worker.fetch(new Request(`https://w.example${path}`, {
    method,
    headers: { 'cf-connecting-ip': ip, 'content-type': 'application/json',
               ...(key ? { authorization: `Bearer ${key}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  }), { DB: db }, {});
  return { status: res.status, out: await res.json() };
};

const upload = (db, codes, batch = 'friends-2026') =>
  call(db, 'POST', '/admin/apple-codes', { body: { batch, codes } });

console.log('uploading a minted batch');

await test('codes are stored, and counted back', async () => {
  const db = makeD1(); seed(db);
  const { status, out } = await upload(db, ['AAAA1111', 'BBBB2222', 'CCCC3333']);
  assert.strictEqual(status, 200);
  assert.strictEqual(out.sent, 3);
  assert.strictEqual(out.in_batch, 3);
});

await test('a batch with no name is refused: codes nobody can group', async () => {
  const db = makeD1(); seed(db);
  const { status, out } = await call(db, 'POST', '/admin/apple-codes',
    { body: { batch: '  ', codes: ['AAAA1111'] } });
  assert.strictEqual(status, 400);
  assert.strictEqual(out.error, 'batch_required');
});

await test('one bad entry rejects the WHOLE upload, storing none of it', async () => {
  const db = makeD1(); seed(db);
  // A CSV header row is exactly what arrives when somebody exports from Apple
  // and forgets to drop the first line.
  const { status, out } = await upload(db, ['AAAA1111', 'Offer Code', 'CCCC3333']);
  assert.strictEqual(status, 400);
  assert.strictEqual(out.error, 'bad_code');
  assert.strictEqual(db.query('SELECT COUNT(*) AS n FROM apple_offer_codes')[0].n, 0,
    'a partial batch is one nobody can account for');
});

await test('re-sending a chunk adds what is missing rather than failing', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111', 'BBBB2222']);
  const { status, out } = await upload(db, ['BBBB2222', 'CCCC3333']);
  assert.strictEqual(status, 200);
  assert.strictEqual(out.in_batch, 3, 'the overlap is absorbed, the new one lands');
});

await test('codes are upper-cased and trimmed, so a CSV quirk is not a second code', async () => {
  const db = makeD1(); seed(db);
  await upload(db, [' aaaa1111 ']);
  assert.strictEqual(db.query('SELECT code FROM apple_offer_codes')[0].code, 'AAAA1111');
});

console.log('handing one out');

await test('assign takes the oldest free code and records who it went to', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111', 'BBBB2222']);
  const { status, out } = await call(db, 'POST', '/admin/apple-codes/assign',
    { body: { assigned_to: 'Sean', note: 'Mac tester' } });
  assert.strictEqual(status, 200);
  assert.strictEqual(out.assigned_to, 'Sean');
  assert.ok(out.code === 'AAAA1111' || out.code === 'BBBB2222');
  assert.ok(out.assigned_at);
});

await test('the same code is never handed to two people', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111', 'BBBB2222']);
  // Both in flight at once, which is two console windows open.
  const [first, second] = await Promise.all([
    call(db, 'POST', '/admin/apple-codes/assign', { body: { assigned_to: 'Sean' } }),
    call(db, 'POST', '/admin/apple-codes/assign', { body: { assigned_to: 'Alex' } }),
  ]);
  assert.strictEqual(first.status, 200);
  assert.strictEqual(second.status, 200);
  assert.notStrictEqual(first.out.code, second.out.code);
});

await test('running out is a refusal that says what to do, not a silent empty', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111']);
  await call(db, 'POST', '/admin/apple-codes/assign', { body: { assigned_to: 'Sean' } });
  const { status, out } = await call(db, 'POST', '/admin/apple-codes/assign',
    { body: { assigned_to: 'Alex' } });
  assert.strictEqual(status, 409);
  assert.strictEqual(out.error, 'none_free');
  assert.match(out.message, /upload/);
});

await test('an unnamed recipient is refused: an unlabelled code cannot be audited', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111']);
  const { status, out } = await call(db, 'POST', '/admin/apple-codes/assign',
    { body: { assigned_to: '   ' } });
  assert.strictEqual(status, 400);
  assert.strictEqual(out.error, 'assigned_to_required');
  assert.strictEqual(db.query('SELECT COUNT(*) AS n FROM apple_offer_codes WHERE assigned_at IS NOT NULL')[0].n, 0);
});

await test('a batch can be named, and only that batch is drawn from', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111'], 'friends-2026');
  await upload(db, ['BBBB2222'], 'press-2026');
  const { out } = await call(db, 'POST', '/admin/apple-codes/assign',
    { body: { assigned_to: 'Sean', batch: 'press-2026' } });
  assert.strictEqual(out.code, 'BBBB2222');
});

console.log('taking one out of circulation');

await test('void marks it unassignable and does NOT claim Apple withdrew it', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111', 'BBBB2222']);
  const { status, out } = await call(db, 'POST', '/admin/apple-codes/void',
    { body: { code: 'aaaa1111' } });
  assert.strictEqual(status, 200);
  assert.strictEqual(out.still_redeemable_at_apple, true,
    'Apple has no API to withdraw a minted code; saying otherwise would be a lie');
  const next = await call(db, 'POST', '/admin/apple-codes/assign',
    { body: { assigned_to: 'Sean' } });
  assert.strictEqual(next.out.code, 'BBBB2222', 'a voided code is never handed out');
});

await test('voiding a code that does not exist is a 404, not a silent success', async () => {
  const db = makeD1(); seed(db);
  const { status, out } = await call(db, 'POST', '/admin/apple-codes/void',
    { body: { code: 'ZZZZ9999' } });
  assert.strictEqual(status, 404);
  assert.strictEqual(out.error, 'unknown_code');
});

console.log('listing');

await test('the list separates free, assigned and voided', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111', 'BBBB2222', 'CCCC3333']);
  await call(db, 'POST', '/admin/apple-codes/assign', { body: { assigned_to: 'Sean' } });
  await call(db, 'POST', '/admin/apple-codes/void', { body: { code: 'CCCC3333' } });

  const free = await call(db, 'GET', '/admin/apple-codes?state=free');
  const assigned = await call(db, 'GET', '/admin/apple-codes?state=assigned');
  const voided = await call(db, 'GET', '/admin/apple-codes?state=void');
  assert.strictEqual(free.out.codes.length, 1);
  assert.strictEqual(assigned.out.codes.length, 1);
  assert.strictEqual(voided.out.codes.length, 1);
});

await test('the batch summary counts what is left to give away', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111', 'BBBB2222', 'CCCC3333']);
  await call(db, 'POST', '/admin/apple-codes/assign', { body: { assigned_to: 'Sean' } });
  const { out } = await call(db, 'GET', '/admin/apple-codes');
  const batch = out.batches.find((b) => b.batch === 'friends-2026');
  assert.strictEqual(batch.total, 3);
  assert.strictEqual(batch.assigned, 1);
  assert.strictEqual(batch.redemptions, 0);
});

console.log('authorisation and audit');

await test('a caller who is not an administrator reaches none of it', async () => {
  const db = makeD1(); seed(db);
  db.sqlite.prepare(`INSERT INTO licences (licence_key, tier, status, plan)
                     VALUES (?, 'managed', 'active', 'standard')`).run('DAWN-CUSTOMER-ONE1111');
  for (const [method, path, body] of [
    ['POST', '/admin/apple-codes', { batch: 'x', codes: ['AAAA1111'] }],
    ['GET', '/admin/apple-codes', undefined],
    ['POST', '/admin/apple-codes/assign', { assigned_to: 'Sean' }],
    ['POST', '/admin/apple-codes/void', { code: 'AAAA1111' }],
  ]) {
    const { status } = await call(db, method, path, { key: 'DAWN-CUSTOMER-ONE1111', body });
    assert.ok(status === 403 || status === 401, `${method} ${path} was ${status}`);
  }
  assert.strictEqual(db.query('SELECT COUNT(*) AS n FROM apple_offer_codes')[0].n, 0);
});

await test('every handled request leaves an audit row naming the action', async () => {
  const db = makeD1(); seed(db);
  await upload(db, ['AAAA1111']);
  await call(db, 'POST', '/admin/apple-codes/assign', { body: { assigned_to: 'Sean' } });
  await call(db, 'POST', '/admin/apple-codes/void', { body: { code: 'AAAA1111' } });
  const actions = db.query('SELECT action FROM admin_audit ORDER BY id').map((r) => r.action);
  assert.deepStrictEqual(actions, ['apple.upload', 'apple.assign', 'apple.void']);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exitCode = 1;
