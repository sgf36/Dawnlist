/**
 * The two admin endpoints that exist because a stored secret proves nothing.
 *
 * `/admin/selftest` asks each upstream a cheap read-only question. It found a
 * real fault within a minute of the credentials being set: the Paddle key
 * AUTHENTICATED (not 401) and was REFUSED (403), because it lacked permission
 * to read customers. A check that only asked "is a value set" would have said
 * yes. Without it the Worker would have known a licence was owed to somebody
 * it could not name, and the only trace would have been a log line nobody was
 * watching — found by a customer who paid and heard nothing.
 *
 * `/admin/resend` is a support tool before it is a test. The commonest thing a
 * paying customer will ever ask for is the key they lost.
 *
 * Runs against a real SQLite D1.
 */
import assert from 'node:assert';
import { handleAdmin, handleRedeem } from '../src/codes.js';
import { newLicenceKey } from '../src/paddle.js';
import { makeD1 } from './d1.mjs';
import { seedCode } from './seed.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const makeDB = () => makeD1();

const post = (path, body, auth) => new Request(`https://x${path}`, {
  method: 'POST',
  body: JSON.stringify(body),
  headers: {
    'cf-connecting-ip': '1.2.3.4',
    ...(auth ? { authorization: `Bearer ${auth}` } : {}),
  },
});
const get = (path, auth) => new Request(`https://x${path}`, {
  headers: auth ? { authorization: `Bearer ${auth}` } : {},
});

async function seedAdmin(db) {
  const c = seedCode(db, { note: 'owner', role: 'admin', plan: 'standard' });
  const res = await handleRedeem(post('/redeem', { code: c.code }), { DB: db }, newLicenceKey);
  return (await res.json()).licence_key;
}

function seedLicence(db, key, status) {
  db.sqlite.prepare(
    "INSERT INTO licences (licence_key, tier, status, plan) VALUES (?, 'managed', ?, 'standard')"
  ).run(key, status);
}

/** Replace fetch for one call, and always put it back. */
async function withFetch(fn, handler) {
  const real = globalThis.fetch;
  globalThis.fetch = handler;
  try { return await fn(); } finally { globalThis.fetch = real; }
}

console.log('\nselftest');

await test('an unauthenticated caller is refused', async () => {
  const res = await handleAdmin(get('/admin/selftest'), { DB: makeDB() });
  assert.equal(res.status, 401);
});

await test('a licence without the admin role is refused', async () => {
  const db = makeDB();
  seedLicence(db, 'DAWN-PLAIN', 'active');
  const res = await handleAdmin(get('/admin/selftest', 'DAWN-PLAIN'), { DB: db });
  assert.equal(res.status, 403);
});

await test('a working pair of credentials reports ok', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await withFetch(
    async () => (await handleAdmin(get('/admin/selftest', licence),
      { DB: db, RESEND_API_KEY: 'r', PADDLE_API_KEY: 'p' })).json(),
    async () => ({ ok: true, status: 200 }));
  assert.equal(body.ok, true);
  assert.equal(body.checks.resend.status, 200);
  assert.equal(body.checks.paddle.status, 200);
});

await test('a key that authenticates but is NOT PERMITTED fails the check', async () => {
  // The real fault on the real Worker: 403 rather than 401. This is the case
  // the endpoint was written for.
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await withFetch(
    async () => (await handleAdmin(get('/admin/selftest', licence),
      { DB: db, RESEND_API_KEY: 'r', PADDLE_API_KEY: 'p' })).json(),
    async (url) => (String(url).includes('paddle')
      ? { ok: false, status: 403 }
      : { ok: true, status: 200 }));
  assert.equal(body.ok, false, 'one broken credential must fail the whole check');
  assert.equal(body.checks.paddle.status, 403);
  assert.equal(body.checks.resend.ok, true, 'the working one still reads as working');
});

await test('an unset credential is reported as unset, not as failing', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await (await handleAdmin(get('/admin/selftest', licence), { DB: db })).json();
  assert.equal(body.checks.resend.reason, 'not set');
  assert.equal(body.checks.paddle.reason, 'not set');
});

await test('the feed key is never CALLED, because a feed request costs a credit', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await withFetch(
    async () => (await handleAdmin(get('/admin/selftest', licence),
      { DB: db, THEIRSTACK_API_KEY: 't' })).json(),
    async (url) => {
      assert.ok(!String(url).includes('theirstack'), 'must not spend a credit');
      return { ok: true, status: 200 };
    });
  assert.equal(body.checks.theirstack.checked, false);
});

await test('a Resend key restricted to sending is reported healthy, and as restricted', async () => {
  // A send-only key is the right key for a Worker that only sends. It cannot
  // list domains, so the check used to call it broken.
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await withFetch(
    async () => (await handleAdmin(get('/admin/selftest', licence),
      { DB: db, RESEND_API_KEY: 're_send_only', PADDLE_API_KEY: 'p' })).json(),
    async (url) => (String(url).includes('resend')
      ? { ok: false, status: 401, text: async () => JSON.stringify({
          name: 'restricted_api_key', message: 'This API key is restricted to only send emails' }) }
      : { ok: true, status: 200 }));
  assert.equal(body.checks.resend.ok, true);
  assert.equal(body.checks.resend.restricted, true);
  assert.equal(body.ok, true);
});

await test('negative control: a Resend key refused for any other reason still fails', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await withFetch(
    async () => (await handleAdmin(get('/admin/selftest', licence),
      { DB: db, RESEND_API_KEY: 're_wrong', PADDLE_API_KEY: 'p' })).json(),
    async (url) => (String(url).includes('resend')
      ? { ok: false, status: 401, text: async () => '{"name":"invalid_api_key"}' }
      : { ok: true, status: 200 }));
  assert.equal(body.checks.resend.ok, false);
  assert.equal(body.checks.resend.status, 401);
  assert.equal(body.ok, false);
});

await test('one check that throws cannot take the others down with it', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const res = await withFetch(
    async () => handleAdmin(get('/admin/selftest', licence),
      { DB: db, RESEND_API_KEY: 'r', PADDLE_API_KEY: 'p' }),
    async (url) => {
      if (String(url).includes('paddle')) throw new TypeError('fetch failed');
      return { ok: true, status: 200 };
    });
  assert.equal(res.status, 200, 'a broken upstream is a finding, not a 500');
  const body = await res.json();
  assert.equal(body.checks.paddle.ok, false);
  assert.equal(body.checks.paddle.reason, 'unreachable');
  assert.equal(body.checks.resend.ok, true, 'the other answer is still there');
  assert.equal(body.ok, false);
});

await test('a failure body that cannot be read is reported by status, not thrown', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await withFetch(
    async () => (await handleAdmin(get('/admin/selftest', licence),
      { DB: db, RESEND_API_KEY: 'r', PADDLE_API_KEY: 'p' })).json(),
    async (url) => (String(url).includes('resend')
      ? { ok: false, status: 500, text: async () => '<html>gateway</html>' }
      : { ok: true, status: 200 }));
  assert.deepEqual(body.checks.resend, { ok: false, status: 500 });
});

console.log('\nresend');

await test('both a licence and an address are required', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const res = await handleAdmin(post('/admin/resend', { to: 'a@b.test' }, licence), { DB: db });
  assert.equal(res.status, 400);
});

await test('an unknown licence is a 404, not a silent success', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const res = await handleAdmin(
    post('/admin/resend', { licence_key: 'DAWN-NOPE', to: 'a@b.test' }, licence), { DB: db });
  assert.equal(res.status, 404);
});

await test('a revoked licence is refused rather than re-sent', async () => {
  // Re-sending a dead key has somebody paste it in and be refused, with no way
  // to tell that from the key being wrong.
  const db = makeDB();
  const licence = await seedAdmin(db);
  seedLicence(db, 'DAWN-DEAD', 'expired');
  const res = await handleAdmin(
    post('/admin/resend', { licence_key: 'DAWN-DEAD', to: 'a@b.test' }, licence), { DB: db });
  assert.equal(res.status, 409);
  assert.equal((await res.json()).error, 'licence_inactive');
});

await test('a working send returns the id and language, and never the address', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const body = await withFetch(
    async () => (await handleAdmin(
      post('/admin/resend', { licence_key: licence, to: 'a@b.test', lang: 'fr' }, licence),
      { DB: db, RESEND_API_KEY: 'r' })).json(),
    async () => ({ ok: true, status: 200, text: async () => '{"id":"re_9"}' }));
  assert.equal(body.ok, true);
  assert.equal(body.id, 're_9');
  assert.equal(body.lang, 'fr');
  assert.ok(!JSON.stringify(body).includes('a@b.test'), 'the address is not echoed back');
});

await test('a failed send is a 502 carrying the reason', async () => {
  const db = makeDB();
  const licence = await seedAdmin(db);
  const res = await withFetch(
    async () => handleAdmin(
      post('/admin/resend', { licence_key: licence, to: 'a@b.test' }, licence),
      { DB: db, RESEND_API_KEY: 'r' }),
    async () => ({ ok: false, status: 422, text: async () => 'domain not verified' }));
  assert.equal(res.status, 502);
  assert.ok((await res.json()).detail.includes('domain not verified'));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
