/**
 * Licences and codes that end, and the ones that must not.
 * Run: node test/expiry.test.mjs
 *
 * What these protect, in one line each:
 *   - a trial licence works for fourteen days and is refused after;
 *   - other licences, and an administrator's, have no end date;
 *   - a code minted without an expiry lapses in thirty days, unless it is an
 *     administrator's or the owner's;
 *   - an expiry is stored as ISO 8601, and one that cannot be read is refused
 *     on the way in and fails closed on the way out;
 *   - an expired licence loses the admin console as well as the feed.
 *
 * Every check drives the real `fetch` against a real SQLite D1, with the clock
 * moved rather than the stored dates, so the comparison under test is the one
 * the Worker makes.
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

const DAY = 24 * 60 * 60 * 1000;
const RealDate = Date;

/** Runs `fn` with the clock `offsetMs` ahead of now. */
async function later(offsetMs, fn) {
  const ms = RealDate.now() + offsetMs;
  globalThis.Date = class extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(ms); }
    static now() { return ms; }
  };
  try { return await fn(); } finally { globalThis.Date = RealDate; }
}

const call = (DB, path, { key, body } = {}) => worker.fetch(new Request(`https://w.example${path}`, {
  method: body ? 'POST' : 'GET',
  headers: { 'cf-connecting-ip': '1.2.3.4', 'content-type': 'application/json',
             ...(key ? { authorization: `Bearer ${key}` } : {}) },
  body: body ? JSON.stringify(body) : undefined,
}), { DB }, {});

async function redeem(DB, codeOver) {
  const code = seedCode(DB, codeOver);
  const res = await call(DB, '/redeem', { body: { code: code.code } });
  const out = await res.json();
  assert.equal(res.status, 200, JSON.stringify(out));
  return out.licence_key;
}

const licenceStatus = async (DB, key) => (await call(DB, '/v1/licence', { key })).status;
const expiryOf = (DB, key) =>
  DB.query('SELECT expires_at FROM licences WHERE licence_key = ?', key)[0].expires_at;

function seedAdmin(DB, { expiresAt = null } = {}) {
  DB.sqlite.prepare(`INSERT INTO licences (licence_key, tier, status, plan, expires_at)
                     VALUES ('DAWN-ADMIN', 'managed', 'active', 'owner', ?)`).run(expiresAt);
  DB.sqlite.exec("INSERT INTO licence_roles (licence_key, role) VALUES ('DAWN-ADMIN', 'admin')");
  return 'DAWN-ADMIN';
}

const nearly = (iso, expectedMs, label) => assert.ok(
  Math.abs(RealDate.parse(iso) - expectedMs) < 60 * 1000, `${label}: ${iso}`);

console.log('licences');

await test('a trial licence works for fourteen days and is refused after', async () => {
  const DB = makeD1();
  const key = await redeem(DB, { role: 'managed', plan: 'trial' });
  nearly(expiryOf(DB, key), RealDate.now() + 14 * DAY, 'written on issue');

  assert.equal(await licenceStatus(DB, key), 200, 'day one');
  assert.equal(await later(13 * DAY, () => licenceStatus(DB, key)), 200, 'day thirteen');
  const res = await later(14 * DAY + 60 * 1000, () => call(DB, '/v1/licence', { key }));
  assert.equal(res.status, 403);
  const out = await res.json();
  assert.equal(out.error, 'licence_inactive', 'the code the app already handles');
  assert.match(out.message, /expired on \d{4}-\d{2}-\d{2}/);
});

await test('an expired trial cannot search either', async () => {
  const DB = makeD1();
  const key = await redeem(DB, { role: 'managed', plan: 'trial' });
  const res = await later(15 * DAY, () => call(DB, '/v1/search', { key, body: { titles: ['a'] } }));
  assert.equal(res.status, 403);
});

await test('a standard licence from a code has no end date', async () => {
  const DB = makeD1();
  const key = await redeem(DB, { role: 'managed', plan: 'standard' });
  assert.equal(expiryOf(DB, key), null);
  assert.equal(await later(400 * DAY, () => licenceStatus(DB, key)), 200);
});

await test('a trial-plan code redeemed by an administrator does not make the licence end', async () => {
  const DB = makeD1();
  const key = await redeem(DB, { role: 'admin', plan: 'trial' });
  assert.equal(expiryOf(DB, key), null);
});

await test('a licence whose expiry cannot be read is refused, not made permanent', async () => {
  const DB = makeD1();
  DB.sqlite.exec(`INSERT INTO licences (licence_key, tier, status, expires_at)
                  VALUES ('DAWN-BADDATE', 'managed', 'active', 'soon-ish')`);
  assert.equal(await licenceStatus(DB, 'DAWN-BADDATE'), 403);
});

await test('positive control: a licence with no expiry, as every existing one has, still works', async () => {
  const DB = makeD1();
  DB.sqlite.exec(`INSERT INTO licences (licence_key, tier, status)
                  VALUES ('DAWN-OLD', 'managed', 'active')`);
  assert.equal(await licenceStatus(DB, 'DAWN-OLD'), 200);
});

await test('an expired administrator licence loses the console', async () => {
  const DB = makeD1();
  const admin = seedAdmin(DB, { expiresAt: new RealDate(RealDate.now() - DAY).toISOString() });
  assert.equal((await call(DB, '/admin/codes', { key: admin })).status, 403);
});

await test('positive control: an unexpired administrator licence reaches it', async () => {
  const DB = makeD1();
  const admin = seedAdmin(DB, { expiresAt: new RealDate(RealDate.now() + DAY).toISOString() });
  assert.equal((await call(DB, '/admin/codes', { key: admin })).status, 200);
});

console.log('\ncodes');

async function mint(DB, admin, body) {
  const res = await call(DB, '/admin/codes', { key: admin, body: { note: 'for a test', ...body } });
  return { status: res.status, out: await res.json() };
}

await test('a code minted without an expiry lapses in thirty days, for byo and managed', async () => {
  const DB = makeD1();
  const admin = seedAdmin(DB);
  for (const role of ['byo', 'managed']) {
    const { out } = await mint(DB, admin, { role });
    nearly(out.expires_at, RealDate.now() + 30 * DAY, `${role} response`);
    const stored = DB.query('SELECT expires_at FROM codes WHERE code = ?', out.code)[0].expires_at;
    assert.equal(stored, out.expires_at, 'what is shown is what is stored');
  }
});

await test('administrator codes and owner-plan codes minted without an expiry never lapse', async () => {
  const DB = makeD1();
  const admin = seedAdmin(DB);
  assert.equal((await mint(DB, admin, { role: 'admin' })).out.expires_at, null);
  assert.equal((await mint(DB, admin, { role: 'managed', plan: 'owner' })).out.expires_at, null);
});

await test('a lapsed code cannot be redeemed, and an unlapsed one can', async () => {
  const DB = makeD1();
  const admin = seedAdmin(DB);
  const { out } = await mint(DB, admin, { role: 'managed', plan: 'standard' });
  const at31 = await later(31 * DAY, () => call(DB, '/redeem', { body: { code: out.code } }));
  assert.equal(at31.status, 403);
  const now = await call(DB, '/redeem', { body: { code: out.code } });
  assert.equal(now.status, 200);
});

await test('an explicit expiry is stored as ISO 8601 UTC', async () => {
  const DB = makeD1();
  const admin = seedAdmin(DB);
  assert.equal((await mint(DB, admin, { expires_at: '2026-12-01' })).out.expires_at,
    '2026-12-01T00:00:00.000Z');
  assert.equal((await mint(DB, admin, { expires_at: '2026-12-01T09:00:00+01:00' })).out.expires_at,
    '2026-12-01T08:00:00.000Z');
  assert.equal((await mint(DB, admin, { role: 'admin', expires_at: '2027-01-01' })).out.expires_at,
    '2027-01-01T00:00:00.000Z', 'an administrator who sets an expiry gets it');
});

await test('an expiry that cannot be read is refused, and nothing is minted', async () => {
  const DB = makeD1();
  const admin = seedAdmin(DB);
  for (const bad of ['next tuesday', 20261201, { d: 1 }]) {
    const { status, out } = await mint(DB, admin, { expires_at: bad });
    assert.equal(status, 400);
    assert.equal(out.error, 'invalid_expires_at');
  }
  assert.equal(DB.query('SELECT COUNT(*) AS n FROM codes')[0].n, 0);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
