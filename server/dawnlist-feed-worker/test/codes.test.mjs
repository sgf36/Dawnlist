/**
 * Override codes and the admin console, against a real SQLite D1.
 * Run: node test/codes.test.mjs
 */
import assert from 'node:assert';
import { handleAdmin, handleRedeem, newCode } from '../src/codes.js';
import { newLicenceKey } from '../src/paddle.js';
import { makeD1 } from './d1.mjs';
import { seedCode } from './seed.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const makeDB = () => makeD1();
const count = (db, table) => db.query(`SELECT COUNT(*) AS n FROM ${table}`)[0].n;

const post = (path, body, auth) => new Request(`https://x${path}`, {
  method: 'POST', body: JSON.stringify(body),
  headers: { 'cf-connecting-ip': '1.2.3.4', ...(auth ? { authorization: `Bearer ${auth}` } : {}) },
});
const get = (path, auth) => new Request(`https://x${path}`, {
  headers: auth ? { authorization: `Bearer ${auth}` } : {},
});

async function seedAdmin(db) {
  const c = seedCode(db, { role: 'admin', note: 'Spencer' });
  const res = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  const { licence_key } = await res.json();
  return { code: c, licence: licence_key };
}

console.log('\nOverride codes');

await test('a valid code issues a licence', async () => {
  const db = makeDB(); const c = seedCode(db);
  const res = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  assert.equal(res.status, 200);
  assert.equal(count(db, 'licences'), 1);
  assert.equal(count(db, 'redemptions'), 1);
});

await test('formatting never decides whether a code works', async () => {
  const db = makeDB(); const c = seedCode(db);
  const messy = c.code.toLowerCase().replace(/-/g, ' ');
  const res = await handleRedeem(post('/redeem', { code: messy }), { DB: db }, newLicenceKey);
  assert.equal(res.status, 200);
});

await test('a single-use code cannot be redeemed twice', async () => {
  const db = makeDB(); const c = seedCode(db);
  await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  const res = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  assert.equal(res.status, 403);
  // The same answer as an unknown code: "spent" would tell a prober the code
  // was real.
  assert.equal((await res.json()).error, 'invalid_code');
  assert.equal(count(db, 'licences'), 1);
});

await test('a STORE REVIEW code with max_uses > 1 can be reused', async () => {
  // A reviewer may test on several machines, or re-test after a rejection.
  // A spent code turns that into a failed review.
  const db = makeDB(); const c = seedCode(db, { max_uses: 25, note: 'Store review' });
  for (let i = 0; i < 5; i++) {
    const res = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
    assert.equal(res.status, 200, `redemption ${i + 1} failed`);
  }
  assert.equal(count(db, 'licences'), 5);
});

await test('an unknown and a revoked code answer identically', async () => {
  // Distinguishing them tells someone probing that a code once existed.
  const db = makeDB();
  const revoked = seedCode(db, { revoked: 1 });
  const a = await handleRedeem(post('/redeem', { code: revoked.code }), { DB: db }, newLicenceKey);
  const b = await handleRedeem(post('/redeem', { code: 'DL-ZZZZ-ZZZZ-ZZZZ-ZZZZ' }), { DB: db }, newLicenceKey);
  assert.equal(a.status, b.status);
  assert.deepEqual(await a.json(), await b.json());
});

await test('an expired code is refused', async () => {
  const db = makeDB();
  const c = seedCode(db, { expires_at: '2020-01-01T00:00:00Z' });
  const res = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  assert.equal(res.status, 403);
  assert.equal((await res.json()).error, 'invalid_code', 'and says no more than an unknown code');
  assert.equal(count(db, 'licences'), 0);
});

await test('repeated failures are rate limited', async () => {
  const db = makeDB();
  for (let i = 0; i < 20; i++) {
    await handleRedeem(post('/redeem', { code: 'DL-BAD1-BAD1-BAD1-BAD1' }), { DB: db }, newLicenceKey);
  }
  const res = await handleRedeem(post('/redeem', { code: 'DL-BAD1-BAD1-BAD1-BAD1' }), { DB: db }, newLicenceKey);
  assert.equal(res.status, 429);
});

await test('codes avoid characters that are misread aloud', async () => {
  const codes = Array.from({ length: 200 }, () => newCode()).join('');
  for (const ch of ['O', '0', 'I', '1', 'S', '5']) {
    assert.ok(!codes.includes(ch), `${ch} appears in generated codes`);
  }
});

console.log('\nAdmin console');

await test('no licence means no console', async () => {
  const db = makeDB();
  const res = await handleAdmin(get('/admin/codes'), { DB: db });
  assert.equal(res.status, 401);
});

await test('an ordinary licence cannot reach the console', async () => {
  const db = makeDB(); const c = seedCode(db);
  const r = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  const { licence_key } = await r.json();
  const res = await handleAdmin(get('/admin/codes', licence_key), { DB: db });
  assert.equal(res.status, 403);
});

await test('an admin code reaches the console', async () => {
  const db = makeDB(); const { licence } = await seedAdmin(db);
  const res = await handleAdmin(get('/admin/codes', licence), { DB: db });
  assert.equal(res.status, 200);
});

await test('withdrawing an admin takes effect IMMEDIATELY on a valid licence', async () => {
  // The whole reason authority lives in the table rather than the credential.
  const db = makeDB(); const { licence } = await seedAdmin(db);
  assert.equal((await handleAdmin(get('/admin/codes', licence), { DB: db })).status, 200);

  db.sqlite.prepare("UPDATE licence_roles SET role = 'byo' WHERE licence_key = ?").run(licence);
  assert.equal((await handleAdmin(get('/admin/codes', licence), { DB: db })).status, 403);
});

await test('an expired licence loses the console', async () => {
  const db = makeDB(); const { licence } = await seedAdmin(db);
  db.sqlite.prepare("UPDATE licences SET status = 'expired' WHERE licence_key = ?").run(licence);
  assert.equal((await handleAdmin(get('/admin/codes', licence), { DB: db })).status, 403);
});

await test('issuing a code requires a note', async () => {
  const db = makeDB(); const { licence } = await seedAdmin(db);
  const res = await handleAdmin(post('/admin/codes', { role: 'byo' }, licence), { DB: db });
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, 'note_required');
});

await test('issuing a code returns it once, with its role', async () => {
  const db = makeDB(); const { licence } = await seedAdmin(db);
  const res = await handleAdmin(
    post('/admin/codes', { role: 'managed', note: 'Sean pilot', max_uses: 3 }, licence),
    { DB: db });
  const body = await res.json();
  assert.equal(body.role, 'managed');
  assert.equal(body.max_uses, 3);
  assert.ok(body.code.startsWith('DL-'));
});

await test('an unknown role falls back to the least privilege', async () => {
  const db = makeDB(); const { licence } = await seedAdmin(db);
  const res = await handleAdmin(
    post('/admin/codes', { role: 'superuser', note: 'x' }, licence), { DB: db });
  assert.equal((await res.json()).role, 'byo');
});

await test('revoking a code also expires the licences it issued', async () => {
  // A withdrawn code that leaves working licences behind has not been withdrawn.
  const db = makeDB(); const { licence } = await seedAdmin(db);
  const c = seedCode(db, { note: 'pilot' });
  const r = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  const issued = (await r.json()).licence_key;

  await handleAdmin(post('/admin/revoke', { code: c.code }, licence), { DB: db });
  assert.equal(db.query('SELECT revoked FROM codes WHERE code = ?', c.code)[0].revoked, 1);
  assert.equal(db.query('SELECT status FROM licences WHERE licence_key = ?', issued)[0].status,
    'expired');
});

await test('an admin cannot revoke the code they are using', async () => {
  // It would sign them out of the console with nothing on screen to explain why.
  const db = makeDB(); const { code, licence } = await seedAdmin(db);
  const res = await handleAdmin(post('/admin/revoke', { code: code.code }, licence), { DB: db });
  assert.equal(res.status, 409);
  assert.equal((await res.json()).error, 'would_revoke_self');
});

await test('the code list reports usage counted from redemptions', async () => {
  const db = makeDB(); const { licence } = await seedAdmin(db);
  const c = seedCode(db, { max_uses: 5 });
  await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);

  const res = await handleAdmin(get('/admin/codes', licence), { DB: db });
  const listed = (await res.json()).codes.find(x => x.code === c.code);
  assert.equal(listed.uses, 2);
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
