/**
 * Redeeming a code: once per use, one answer for every refusal, one row read.
 * Run: node test/redemption.test.mjs
 *
 * What these protect, in one line each:
 *   - parallel redemptions cannot spend a code more times than max_uses;
 *   - a redemption refused part-way leaves no licence, role or redemption;
 *   - unknown, revoked, expired and spent codes answer identically;
 *   - a code is found through its index, not by reading every code;
 *   - a code minted before migration 007 still redeems after it.
 */
import assert from 'node:assert';
import { handleAdmin, handleRedeem, newCode } from '../src/codes.js';
import { newLicenceKey } from '../src/paddle.js';
import { fixture, makeD1, migrations } from './d1.mjs';
import { barrier } from './interleave.mjs';
import { seedCode } from './seed.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const count = (db, table) => db.query(`SELECT COUNT(*) AS n FROM ${table}`)[0].n;
const redeemRequest = (code, ip = '1.2.3.4') => new Request('https://w/redeem', {
  method: 'POST', body: JSON.stringify({ code }),
  headers: { 'cf-connecting-ip': ip, 'content-type': 'application/json' },
});
async function redeem(DB, code, ip) {
  const res = await handleRedeem(redeemRequest(code, ip), { DB }, newLicenceKey);
  return { status: res.status, out: await res.json() };
}

console.log('the use limit');

await test('five parallel redemptions of a single-use code issue exactly one licence', async () => {
  const db = makeD1();
  const code = seedCode(db, { max_uses: 1 });
  const racing = barrier(db, /INSERT INTO licences/, 5);
  const results = await Promise.all(Array.from({ length: 5 }, (_, i) =>
    redeem(racing, code.code, `10.0.0.${i}`)));

  assert.equal(results.filter((r) => r.status === 200).length, 1);
  assert.equal(count(db, 'licences'), 1, 'one licence');
  assert.equal(count(db, 'redemptions'), 1, 'one redemption');
  assert.equal(count(db, 'licence_roles'), 1, 'one role, and no orphan rows');
});

await test('positive control: a three-use code redeemed five times at once issues exactly three', async () => {
  const db = makeD1();
  const code = seedCode(db, { max_uses: 3 });
  const racing = barrier(db, /INSERT INTO licences/, 5);
  const results = await Promise.all(Array.from({ length: 5 }, (_, i) =>
    redeem(racing, code.code, `10.0.1.${i}`)));
  assert.equal(results.filter((r) => r.status === 200).length, 3);
  assert.equal(count(db, 'licences'), 3);
  assert.equal(count(db, 'redemptions'), 3);
});

await test('a code revoked while it is being redeemed issues nothing, and leaves nothing', async () => {
  const db = makeD1();
  const code = seedCode(db);
  // The revocation lands after the code was read and before it is spent.
  const revokedMidway = { ...db, batch: async (stmts) => {
    db.sqlite.prepare('UPDATE codes SET revoked = 1 WHERE code = ?').run(code.code);
    return db.batch(stmts);
  } };
  const { status, out } = await redeem(revokedMidway, code.code);
  assert.equal(status, 403);
  assert.equal(out.error, 'invalid_code');
  assert.deepEqual([count(db, 'licences'), count(db, 'redemptions'), count(db, 'licence_roles')],
    [0, 0, 0]);
});

console.log('\none answer for every refusal');

await test('unknown, revoked, expired and spent codes answer identically', async () => {
  const db = makeD1();
  const revoked = seedCode(db, { revoked: 1 });
  const expired = seedCode(db, { expires_at: '2020-01-01T00:00:00.000Z' });
  const spent = seedCode(db, { max_uses: 1 });
  assert.equal((await redeem(db, spent.code, '10.1.0.1')).status, 200, 'spend it first');

  const answers = [];
  for (const [label, code] of [['unknown', 'DL-ZZZZ-ZZZZ-ZZZZ-ZZZZ'], ['revoked', revoked.code],
                               ['expired', expired.code], ['spent', spent.code]]) {
    answers.push({ label, ...(await redeem(db, code, `10.1.1.${answers.length}`)) });
  }
  for (const a of answers) {
    assert.deepEqual({ status: a.status, out: a.out }, { status: 403, out: { error: 'invalid_code' } },
      `${a.label} gave a different answer`);
  }
});

await test('positive control: a live code with uses left is accepted', async () => {
  const db = makeD1();
  const live = seedCode(db, { max_uses: 2, expires_at: '2099-01-01T00:00:00.000Z' });
  assert.equal((await redeem(db, live.code)).status, 200);
});

console.log('\nfinding the code');

/** Captures every statement prepared, with its bound parameters. */
function recording(db) {
  const seen = [];
  const wrap = (stmt, entry) => ({ ...stmt,
    bind: (...args) => { entry.params = args; return wrap(stmt.bind(...args), entry); } });
  return { seen, d1: { ...db, prepare: (sql) => {
    const entry = { sql, params: [] };
    seen.push(entry);
    return wrap(db.prepare(sql), entry);
  } } };
}

const plan = (db, sql, params = []) => db.sqlite.prepare(`EXPLAIN QUERY PLAN ${sql}`)
  .all(...params).map((r) => r.detail).join(' | ');

await test('a redemption reads codes through the index, never by scanning them', async () => {
  const db = makeD1();
  for (let i = 0; i < 20; i++) seedCode(db);
  const target = seedCode(db);
  const { seen, d1 } = recording(db);
  assert.equal((await redeem(d1, target.code.toLowerCase().replace(/-/g, ' '))).status, 200);

  const reads = seen.filter((s) => /^\s*SELECT\b/i.test(s.sql) && /\bFROM codes\b/.test(s.sql));
  assert.ok(reads.length > 0, 'the code was read');
  for (const r of reads) {
    assert.doesNotMatch(plan(db, r.sql, r.params), /SCAN codes/, r.sql);
  }
});

await test('positive control: the detector does see a scan when there is one', async () => {
  assert.match(plan(makeD1(), 'SELECT * FROM codes'), /SCAN codes/);
});

await test('revoking finds the code however it is typed', async () => {
  const db = makeD1();
  db.sqlite.exec(`INSERT INTO licences (licence_key, tier, status) VALUES ('DAWN-ADM', 'managed', 'active');
                  INSERT INTO licence_roles (licence_key, role) VALUES ('DAWN-ADM', 'admin');`);
  const code = seedCode(db);
  const res = await handleAdmin(new Request('https://w/admin/revoke', {
    method: 'POST', body: JSON.stringify({ code: code.code.toLowerCase().replace(/-/g, '') }),
    headers: { authorization: 'Bearer DAWN-ADM' },
  }), { DB: db });
  assert.equal(res.status, 200);
  assert.equal(db.query('SELECT revoked FROM codes WHERE code = ?', code.code)[0].revoked, 1);
});

await test('a code minted before migration 007 redeems after it, however it is typed', async () => {
  const db = makeD1({ sql: fixture('schema-as-deployed-2026-09-06.sql') });
  const all = migrations();
  for (const m of all.filter((m) => m.name < '007')) db.sqlite.exec(m.sql);
  const code = newCode();
  db.sqlite.prepare(
    `INSERT INTO codes (code, note, max_uses, revoked, created_at, role, plan)
     VALUES (?, 'minted before 007', 1, 0, '2026-09-08 10:00:00', 'managed', 'standard')`
  ).run(code);
  for (const m of all.filter((m) => m.name >= '007')) db.sqlite.exec(m.sql);

  const { status } = await redeem(db, code.toLowerCase().replace(/-/g, ' '));
  assert.equal(status, 200, 'the backfill agrees with normalise()');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
