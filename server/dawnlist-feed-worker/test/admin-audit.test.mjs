/**
 * What the admin console shows, records and allows.
 * Run: node test/admin-audit.test.mjs
 *
 * What these protect, in one line each:
 *   - no admin listing prints a licence key, only its last four characters;
 *   - every handled admin request leaves one audit row: action, a hash of who,
 *     a masked target, a time — and no content;
 *   - a caller who is not an administrator is never recorded as one;
 *   - one administrator is slowed after sixty requests in ten minutes;
 *   - repeated failed authentication is locked out per connection.
 */
import assert from 'node:assert';
import worker from '../src/index.js';
import { makeD1 } from './d1.mjs';
import { seedCode } from './seed.mjs';

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
  const lic = db.sqlite.prepare(`INSERT INTO licences (licence_key, tier, status, plan)
                                 VALUES (?, 'managed', 'active', 'standard')`);
  lic.run('DAWN-CUSTOMER-ONE1111');
  lic.run('DAWN-CUSTOMER-TWO2222');
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

const sha256 = async (text) => [...new Uint8Array(
  await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))]
  .map((b) => b.toString(16).padStart(2, '0')).join('');

const auditRows = (db) => db.query('SELECT at, action, actor_hash, target FROM admin_audit ORDER BY id');

console.log('masking');

await test('/admin/licences shows the last four characters of each key, never a key', async () => {
  const db = makeD1();
  seed(db);
  const { status, out } = await call(db, 'GET', '/admin/licences');
  assert.equal(status, 200);
  assert.equal(out.licences.length, 3, 'positive control: every licence is listed');
  const text = JSON.stringify(out);
  for (const key of [ADMIN, 'DAWN-CUSTOMER-ONE1111', 'DAWN-CUSTOMER-TWO2222']) {
    assert.ok(!text.includes(key), `${key} printed in full`);
  }
  assert.deepEqual(out.licences.map((l) => l.licence).sort(), ['…1111', '…2222', '…CCCC']);
});

console.log('\nthe audit');

await test('every handled request is recorded: action, a hash of who, a masked target, a time', async () => {
  const db = makeD1();
  seed(db);
  const target = seedCode(db, { note: 'to revoke' });
  const minted = await call(db, 'POST', '/admin/codes', { body: { note: 'for a tester' } });
  await call(db, 'GET', '/admin/codes');
  await call(db, 'POST', '/admin/revoke', { body: { code: target.code } });
  await call(db, 'GET', '/admin/licences');
  await call(db, 'GET', '/admin/undelivered');

  const rows = auditRows(db);
  assert.deepEqual(rows.map((r) => r.action),
    ['codes.mint', 'codes.list', 'codes.revoke', 'licences.list', 'undelivered.list']);
  const actor = await sha256(ADMIN);
  assert.ok(rows.every((r) => r.actor_hash === actor), 'who, as a hash of the key');
  assert.ok(rows.every((r) => r.at), 'and when');
  assert.equal(rows[0].target, `…${minted.out.code.slice(-4)}`, 'the minted code, masked');
  assert.equal(rows[2].target, `…${target.code.slice(-4)}`, 'the revoked code, masked');
  assert.equal(rows[1].target, null, 'a listing has no single target');
});

await test('the audit holds no key, no code and no content', async () => {
  const db = makeD1();
  seed(db);
  const minted = await call(db, 'POST', '/admin/codes',
    { body: { note: 'for Jane Doe, jane@example.test' } });
  const text = JSON.stringify(auditRows(db));
  for (const secret of [ADMIN, minted.out.code, 'Jane', 'jane@example.test']) {
    assert.ok(!text.includes(secret), `the audit contains ${secret}`);
  }
});

await test('a caller who is not an administrator is never recorded as one', async () => {
  const db = makeD1();
  seed(db);
  const { status } = await call(db, 'GET', '/admin/codes', { key: 'DAWN-CUSTOMER-ONE1111' });
  assert.equal(status, 403);
  assert.deepEqual(auditRows(db), []);
  assert.equal(db.query('SELECT COUNT(*) AS n FROM code_attempts')[0].n, 1,
    'the refusal is counted as a failed attempt instead');
});

await test('an unknown admin path is refused and not recorded', async () => {
  const db = makeD1();
  seed(db);
  assert.equal((await call(db, 'GET', '/admin/everything')).status, 404);
  assert.deepEqual(auditRows(db), []);
});

console.log('\nlimits');

async function seedRequests(db, n, when) {
  const actor = await sha256(ADMIN);
  const insert = db.sqlite.prepare(
    `INSERT INTO admin_audit (at, action, actor_hash) VALUES (datetime('now', ?), 'codes.list', ?)`);
  for (let i = 0; i < n; i++) insert.run(when, actor);
}

await test('an administrator who has made sixty requests in ten minutes is slowed down', async () => {
  const db = makeD1();
  seed(db);
  await seedRequests(db, 60, '-1 minutes');
  const { status, out } = await call(db, 'GET', '/admin/codes');
  assert.equal(status, 429);
  assert.equal(out.error, 'too_many_requests');
});

await test('positive control: fifty-nine requests are still served', async () => {
  const db = makeD1();
  seed(db);
  await seedRequests(db, 59, '-1 minutes');
  assert.equal((await call(db, 'GET', '/admin/codes')).status, 200);
});

await test('positive control: requests older than ten minutes do not count', async () => {
  const db = makeD1();
  seed(db);
  await seedRequests(db, 200, '-11 minutes');
  assert.equal((await call(db, 'GET', '/admin/codes')).status, 200);
});

await test('repeated failed authentication locks out that connection, not others', async () => {
  const db = makeD1();
  seed(db);
  for (let i = 0; i < 20; i++) {
    await call(db, 'GET', '/admin/codes', { key: `DAWN-GUESS-${i}`, ip: '198.51.100.9' });
  }
  assert.equal((await call(db, 'GET', '/admin/codes',
    { key: 'DAWN-GUESS-X', ip: '198.51.100.9' })).status, 429);
  assert.equal((await call(db, 'GET', '/admin/codes',
    { key: 'DAWN-GUESS-X', ip: '198.51.100.10' })).status, 403,
    'another connection is refused, not locked out');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
