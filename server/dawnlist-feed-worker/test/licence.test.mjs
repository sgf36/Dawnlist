/**
 * `/v1/licence` — the route that exists because `/health` cannot verify.
 *
 * `/health` answers for the SERVICE. It takes no request, reads no
 * Authorization header, and returns 200 to anybody. The desktop app was
 * verifying licences against it, so every string typed into the licence box
 * verified and the app reported "licence verified" for all of them.
 *
 * These tests drive the Worker's real `fetch` export rather than the handler
 * directly, because the routing is half of what was missing: a handler that
 * works and is not reachable is exactly the shape of the bug it replaced.
 *
 * `granted_by_code` vs `purchased` is the other load-bearing part. A Mac App
 * Store build may honour a free grant and must NOT honour a purchase made
 * outside Apple's commerce (guideline 3.1.1). The client cannot tell one
 * licence string from another, so the distinction has to be made here, where
 * `licence_roles.from_code` and `licences.paddle_subscription_id` record it.
 */
import assert from 'node:assert';
import worker from '../src/index.js';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

function makeEnv({ licences = [], roles = [] } = {}) {
  return {
    DB: {
      prepare: (sql) => ({
        _a: [],
        bind(...a) { this._a = a; return this; },
        async first() {
          if (sql.includes('FROM licence_roles')) {
            return roles.find((r) => r.licence_key === this._a[0]) || null;
          }
          if (sql.includes('FROM licences')) {
            return licences.find((l) => l.licence_key === this._a[0]) || null;
          }
          return null;
        },
      }),
    },
  };
}

function get(key) {
  return new Request('https://w.example/v1/licence', {
    headers: key ? { authorization: `Bearer ${key}` } : {},
  });
}

const ACTIVE = {
  licence_key: 'DL-REAL', tier: 'standard', status: 'active', plan: 'standard',
};

await test('a licence nobody issued is refused, not accepted', async () => {
  const res = await worker.fetch(get('anything-at-all'), makeEnv(), {});
  assert.strictEqual(res.status, 403);
  assert.strictEqual((await res.json()).error, 'unknown_licence');
});

await test('no key at all is 401, which the app reads as a refusal', async () => {
  const res = await worker.fetch(get(null), makeEnv(), {});
  assert.strictEqual(res.status, 401);
});

await test('a real licence answers with its plan and role', async () => {
  const env = makeEnv({ licences: [ACTIVE] });
  const res = await worker.fetch(get('DL-REAL'), env, {});
  assert.strictEqual(res.status, 200);
  const body = await res.json();
  assert.strictEqual(body.ok, true);
  assert.strictEqual(body.plan, 'standard');
  assert.strictEqual(body.role, 'byo', 'no role row means the smallest role');
});

await test('a suspended licence is refused even though it exists', async () => {
  const env = makeEnv({ licences: [{ ...ACTIVE, status: 'suspended' }] });
  const res = await worker.fetch(get('DL-REAL'), env, {});
  assert.strictEqual(res.status, 403);
  assert.strictEqual((await res.json()).error, 'licence_inactive');
});

await test('a code-granted licence says so, and says it was not purchased', async () => {
  const env = makeEnv({
    licences: [ACTIVE],
    roles: [{ licence_key: 'DL-REAL', role: 'managed', from_code: 'FRIEND-1' }],
  });
  const body = await (await worker.fetch(get('DL-REAL'), env, {})).json();
  assert.strictEqual(body.granted_by_code, true);
  assert.strictEqual(body.purchased, false,
    'a free grant is not a purchase — this is what guideline 3.1.1 turns on');
  assert.strictEqual(body.role, 'managed');
});

await test('a Paddle licence reports purchased, and not code-granted', async () => {
  const env = makeEnv({
    licences: [{ ...ACTIVE, paddle_subscription_id: 'sub_01' }],
  });
  const body = await (await worker.fetch(get('DL-REAL'), env, {})).json();
  assert.strictEqual(body.purchased, true);
  assert.strictEqual(body.granted_by_code, false,
    'a Mac build must not honour a purchase made outside Apple commerce');
});

await test('the route is GET only', async () => {
  const res = await worker.fetch(
    new Request('https://w.example/v1/licence', { method: 'POST' }),
    makeEnv({ licences: [ACTIVE] }), {});
  assert.strictEqual(res.status, 404);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
