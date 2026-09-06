/**
 * Override codes and the admin console, against a stubbed D1.
 * Run: node test/codes.test.mjs
 */
import assert from 'node:assert';
import { handleAdmin, handleRedeem, newCode } from '../src/codes.js';
import { newLicenceKey } from '../src/paddle.js';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

/** D1 stub with just enough SQL awareness for these paths. */
function makeDB() {
  const db = { codes: [], licences: [], roles: [], redemptions: [], attempts: [], usage: [] };
  db.prepare = (sql) => ({
    _a: [],
    bind(...a) { this._a = a; return this; },
    async first() {
      if (sql.includes('FROM code_attempts')) {
        const r = db.attempts.find(x => x.ip === this._a[0] && x.day === this._a[1]);
        return r || null;
      }
      if (sql.includes('COUNT(*) AS n FROM redemptions')) {
        return { n: db.redemptions.filter(r => r.code === this._a[0]).length };
      }
      if (sql.includes('FROM licences WHERE licence_key')) {
        return db.licences.find(l => l.licence_key === this._a[0]) || null;
      }
      if (sql.includes('FROM licence_roles WHERE licence_key')) {
        return db.roles.find(r => r.licence_key === this._a[0]) || null;
      }
      return null;
    },
    async all() {
      if (sql.includes('FROM codes c')) {
        return { results: db.codes.map(c => ({ ...c,
          uses: db.redemptions.filter(r => r.code === c.code).length })) };
      }
      if (sql.includes('SELECT * FROM codes') || sql.includes('SELECT code FROM codes')) {
        return { results: db.codes };
      }
      if (sql.includes('FROM licences l')) {
        return { results: db.licences.map(l => ({ ...l,
          role: db.roles.find(r => r.licence_key === l.licence_key)?.role || null })) };
      }
      return { results: [] };
    },
    async run() {
      if (sql.includes('INSERT INTO code_attempts')) {
        const e = db.attempts.find(x => x.ip === this._a[0] && x.day === this._a[1]);
        if (e) e.failures += 1; else db.attempts.push({ ip: this._a[0], day: this._a[1], failures: 1 });
      } else if (sql.includes('INSERT INTO licences')) {
        db.licences.push({ licence_key: this._a[0], tier: this._a[1], status: 'active', created_at: 'now' });
      } else if (sql.includes('INSERT INTO licence_roles')) {
        db.roles.push({ licence_key: this._a[0], role: this._a[1], from_code: this._a[2] });
      } else if (sql.includes('INSERT INTO redemptions')) {
        db.redemptions.push({ code: this._a[0], licence_key: this._a[1] });
      } else if (sql.includes('INSERT INTO codes')) {
        db.codes.push({ code: this._a[0], note: this._a[1], max_uses: this._a[2],
                        revoked: 0, created_at: 'now', expires_at: this._a[3], role: this._a[4] });
      } else if (sql.includes('UPDATE codes SET revoked')) {
        const c = db.codes.find(c => c.code === this._a[0]); if (c) c.revoked = 1;
      } else if (sql.includes("UPDATE licences SET status = 'expired' WHERE licence_key IN")) {
        const keys = db.redemptions.filter(r => r.code === this._a[0]).map(r => r.licence_key);
        for (const l of db.licences) if (keys.includes(l.licence_key)) l.status = 'expired';
      }
      return {};
    },
  });
  return db;
}

const post = (path, body, auth) => new Request(`https://x${path}`, {
  method: 'POST', body: JSON.stringify(body),
  headers: { 'cf-connecting-ip': '1.2.3.4', ...(auth ? { authorization: `Bearer ${auth}` } : {}) },
});
const get = (path, auth) => new Request(`https://x${path}`, {
  headers: auth ? { authorization: `Bearer ${auth}` } : {},
});

function seedCode(db, over = {}) {
  const c = { code: newCode(), note: 'test', max_uses: 1, revoked: 0,
              created_at: 'now', expires_at: null, role: 'byo', ...over };
  db.codes.push(c);
  return c;
}

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
  assert.equal(db.licences.length, 1);
  assert.equal(db.redemptions.length, 1);
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
  assert.equal((await res.json()).error, 'code_spent');
});

await test('a STORE REVIEW code with max_uses > 1 can be reused', async () => {
  // A reviewer may test on several machines, or re-test after a rejection.
  // A spent code turns that into a failed review.
  const db = makeDB(); const c = seedCode(db, { max_uses: 25, note: 'Store review' });
  for (let i = 0; i < 5; i++) {
    const res = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
    assert.equal(res.status, 200, `redemption ${i + 1} failed`);
  }
  assert.equal(db.licences.length, 5);
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
  assert.equal((await res.json()).error, 'expired_code');
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

  db.roles.find(r => r.licence_key === licence).role = 'byo';
  assert.equal((await handleAdmin(get('/admin/codes', licence), { DB: db })).status, 403);
});

await test('an expired licence loses the console', async () => {
  const db = makeDB(); const { licence } = await seedAdmin(db);
  db.licences.find(l => l.licence_key === licence).status = 'expired';
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
  assert.equal(db.codes.find(x => x.code === c.code).revoked, 1);
  assert.equal(db.licences.find(l => l.licence_key === issued).status, 'expired');
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
