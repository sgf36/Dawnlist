/**
 * `/v1/apple` — both request shapes, against mocked Apple responses.
 *
 * Driven through the Worker's real `fetch` export, because the route not
 * existing WAS the bug: the Mac build posted to a path the router answered 404,
 * and a handler that works but is not routed is that bug again.
 *
 * Every refusal test is paired with the entitled case it differs from, so a
 * handler that refused everything could not pass the file.
 */
import assert from 'node:assert';
import worker from '../src/index.js';
import { appStoreToken, decodeJws } from '../src/apple.js';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.stack}`); failed++; }
}

// -- fixtures ---------------------------------------------------------------

const DAY = 24 * 60 * 60 * 1000;
const BUNDLE = 'com.spencerfields.dawnlist';
const PRODUCT = 'com.spencerfields.dawnlist.monthly';

const b64u = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');
const jws = (payload) => `${b64u({ alg: 'ES256', x5c: ['not-checked'] })}.${b64u(payload)}.sig`;

const keys = await crypto.subtle.generateKey(
  { name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
const der = Buffer.from(await crypto.subtle.exportKey('pkcs8', keys.privateKey));
const PEM = `-----BEGIN PRIVATE KEY-----\n${der.toString('base64').match(/.{1,64}/g).join('\n')}\n-----END PRIVATE KEY-----\n`;

function makeEnv(over = {}) {
  const db = { apple: new Map(), licences: new Map() };
  const DB = {
    prepare: (sql) => ({
      _a: [],
      bind(...a) { this._a = a; return this; },
      async first() {
        if (sql.includes('SELECT licence_key FROM apple_transactions')) {
          const row = db.apple.get(this._a[0]);
          return row ? { licence_key: row.licence_key } : null;
        }
        return null;
      },
      async run() {
        const a = this._a;
        if (sql.includes('INSERT INTO apple_transactions')) {
          const [id, key, environment, status, expires_at, now] = a;
          const existing = db.apple.get(id);
          if (existing) Object.assign(existing, { environment, status, expires_at, updated_at: now });
          else {
            if ([...db.apple.values()].some((r) => r.licence_key === key)) {
              throw new Error('UNIQUE constraint failed: apple_transactions.licence_key');
            }
            db.apple.set(id, { licence_key: key, environment, status, expires_at });
          }
        } else if (sql.includes('UPDATE apple_transactions')) {
          const [status, expires_at, , id] = a;
          Object.assign(db.apple.get(id), { status, expires_at });
        } else if (sql.includes('INSERT INTO licences')) {
          const [key, plan, maxPostings, , , expires_at] = a;
          const existing = db.licences.get(key);
          if (existing) Object.assign(existing, { status: 'active', expires_at });
          else db.licences.set(key, { tier: 'managed', status: 'active', plan,
                                      max_postings_per_day: maxPostings, expires_at });
        } else if (sql.includes('UPDATE licences SET status')) {
          const [status, expires_at, key] = a;
          Object.assign(db.licences.get(key), { status, expires_at });
        } else {
          throw new Error(`unexpected SQL in test: ${sql}`);
        }
        return {};
      },
    }),
  };
  const env = {
    DB,
    APPLE_BUNDLE_ID: BUNDLE,
    APPLE_PRODUCT_ID: PRODUCT,
    APPLE_SHARED_SECRET: 'shared-secret',
    APPLE_IAP_KEY_ID: 'KEY123',
    APPLE_IAP_ISSUER_ID: 'issuer-uuid',
    APPLE_IAP_PRIVATE_KEY: PEM,
    ...over,
  };
  return { env, db };
}

/** Replace global fetch with a router; returns the list of calls made. */
function mockApple(route) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    const [status, body] = await route(String(url), init, calls.length);
    return new Response(JSON.stringify(body), { status });
  };
  return calls;
}

function statusBody({ status = 1, productId = PRODUCT, bundleId = BUNDLE,
                      original = '2000000111', expires = Date.now() + 20 * DAY,
                      revocationDate, grace } = {}) {
  return {
    environment: 'Production',
    bundleId,
    data: [{
      subscriptionGroupIdentifier: '22370180',
      lastTransactions: [{
        status,
        originalTransactionId: original,
        signedTransactionInfo: jws({
          bundleId, productId, originalTransactionId: original,
          transactionId: '2000000999', expiresDate: expires, revocationDate,
          environment: 'Production',
        }),
        signedRenewalInfo: jws({ originalTransactionId: original,
                                 gracePeriodExpiresDate: grace }),
      }],
    }],
  };
}

function receiptBody({ status = 0, bundleId = BUNDLE, transactions, renewals = [] } = {}) {
  return {
    status,
    environment: 'Production',
    receipt: { bundle_id: bundleId },
    latest_receipt_info: transactions ?? [{
      product_id: PRODUCT, original_transaction_id: '2000000111',
      transaction_id: '2000000999', expires_date_ms: String(Date.now() + 20 * DAY),
    }],
    pending_renewal_info: renewals,
  };
}

const post = (body) => new Request('https://w.example/v1/apple', {
  method: 'POST', body: JSON.stringify(body),
  headers: { 'content-type': 'application/json' },
});
const exchange = async (env, body) => {
  const res = await worker.fetch(post(body), env, {});
  return { status: res.status, body: await res.json() };
};

const IS_PROD_API = (url) => url.startsWith('https://api.storekit.itunes.apple.com/');
const IS_SANDBOX_API = (url) => url.startsWith('https://api.storekit-sandbox.itunes.apple.com/');

console.log('\n/v1/apple — originalTransactionId, App Store Server API');

await test('an active subscription becomes a managed standard licence', async () => {
  const { env, db } = makeEnv();
  const calls = mockApple(() => [200, statusBody()]);
  const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 200);
  assert.match(body.licence_key, /^DAWN-/);
  assert.strictEqual(body.status, 'active');
  assert.ok(Date.parse(body.expires_at) > Date.now());
  const licence = db.licences.get(body.licence_key);
  assert.deepStrictEqual([licence.tier, licence.plan, licence.status],
    ['managed', 'standard', 'active']);
  assert.strictEqual(licence.expires_at, body.expires_at);
  assert.strictEqual(db.apple.get('2000000111').licence_key, body.licence_key);
  assert.ok(calls[0].url.endsWith('/inApps/v1/subscriptions/2000000111'));
});

await test('billing grace (status 4) is entitled, until the grace date', async () => {
  const { env } = makeEnv();
  const grace = Date.now() + 10 * DAY;
  mockApple(() => [200, statusBody({ status: 4, expires: Date.now() - DAY, grace })]);
  const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 200);
  assert.strictEqual(body.status, 'grace');
  assert.strictEqual(body.expires_at, new Date(grace).toISOString());
});

for (const [code, name] of [[2, 'expired'], [3, 'billing_retry'], [5, 'revoked']]) {
  await test(`status ${code} (${name}) is refused, and mints no licence`, async () => {
    const { env, db } = makeEnv();
    mockApple(() => [200, statusBody({ status: code, expires: Date.now() - DAY })]);
    const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
    assert.strictEqual(status, 403, 'a refusal the app must not grace');
    assert.strictEqual(body.error, 'not_subscribed');
    assert.strictEqual(body.status, name);
    assert.strictEqual(body.licence_key, undefined);
    assert.strictEqual(db.licences.size, 0);
  });
}

await test('a later revocation switches off the licence it had issued', async () => {
  const { env, db } = makeEnv();
  mockApple(() => [200, statusBody()]);
  const first = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(db.licences.get(first.body.licence_key).status, 'active');

  mockApple(() => [200, statusBody({ status: 5, revocationDate: Date.now() })]);
  const second = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(second.status, 403);
  assert.strictEqual(db.licences.get(first.body.licence_key).status, 'refunded');
});

await test('a transaction for another bundle is refused', async () => {
  const { env } = makeEnv();
  mockApple(() => [200, statusBody({ bundleId: 'com.example.other' })]);
  const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.error, 'wrong_bundle');
});

await test('a transaction for another product is refused', async () => {
  const { env, db } = makeEnv();
  mockApple(() => [200, statusBody({ productId: 'com.spencerfields.dawnlist.yearly' })]);
  const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.error, 'wrong_product');
  assert.strictEqual(db.licences.size, 0);
});

await test('production 404 retries sandbox, which is where App Review buys', async () => {
  const { env } = makeEnv();
  const calls = mockApple((url) => IS_PROD_API(url)
    ? [404, { errorCode: 4040010, errorMessage: 'Transaction id not found.' }]
    : [200, statusBody()]);
  const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 200);
  assert.ok(body.licence_key);
  assert.strictEqual(calls.length, 2);
  assert.ok(IS_PROD_API(calls[0].url) && IS_SANDBOX_API(calls[1].url));
});

await test('a production answer never asks sandbox (positive control)', async () => {
  const { env } = makeEnv();
  const calls = mockApple(() => [200, statusBody()]);
  await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(calls.length, 1);
  assert.ok(IS_PROD_API(calls[0].url));
});

await test('unknown in both environments is a refusal', async () => {
  const { env } = makeEnv();
  mockApple(() => [404, { errorCode: 4040010 }]);
  const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.error, 'unknown_transaction');
});

for (const name of ['APPLE_IAP_KEY_ID', 'APPLE_IAP_ISSUER_ID', 'APPLE_IAP_PRIVATE_KEY',
                    'APPLE_BUNDLE_ID', 'APPLE_PRODUCT_ID']) {
  await test(`without ${name} it is 503 apple_not_configured and Apple is not asked`, async () => {
    const { env } = makeEnv({ [name]: undefined });
    const calls = mockApple(() => [200, statusBody()]);
    const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
    assert.strictEqual(status, 503, 'the app reads 5xx as unreachable, never as unsubscribed');
    assert.strictEqual(body.error, 'apple_not_configured');
    assert.strictEqual(calls.length, 0);
  });
}

await test('the transaction path does not need the receipt secret', async () => {
  const { env } = makeEnv({ APPLE_SHARED_SECRET: undefined });
  mockApple(() => [200, statusBody()]);
  assert.strictEqual((await exchange(env, { originalTransactionId: '2000000111' })).status, 200);
});

await test('Apple refusing OUR key is 502, not a refusal of the customer', async () => {
  const { env } = makeEnv();
  mockApple(() => [401, {}]);
  const { status, body } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 502);
  assert.strictEqual(body.error, 'apple_auth_failed');
});

await test('Apple being down is 502', async () => {
  const { env } = makeEnv();
  globalThis.fetch = async () => { throw new TypeError('network'); };
  const { status } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 502);
});

await test('exchanging twice reuses the one licence', async () => {
  const { env, db } = makeEnv();
  mockApple(() => [200, statusBody()]);
  const a = await exchange(env, { originalTransactionId: '2000000111' });
  const b = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(a.body.licence_key, b.body.licence_key);
  assert.strictEqual(db.licences.size, 1);
  assert.strictEqual(db.apple.size, 1);
});

await test('a non-numeric id is a bad request, not a refusal', async () => {
  const { env } = makeEnv();
  const calls = mockApple(() => [200, statusBody()]);
  const { status } = await exchange(env, { originalTransactionId: '../../admin' });
  assert.strictEqual(status, 400);
  assert.strictEqual(calls.length, 0);
});

await test('the bearer token is an ES256 JWT Apple will accept', async () => {
  const { env } = makeEnv();
  const calls = mockApple(() => [200, statusBody()]);
  await exchange(env, { originalTransactionId: '2000000111' });

  const token = calls[0].init.headers.authorization.replace(/^Bearer /, '');
  const [h, p, s] = token.split('.');
  const header = JSON.parse(Buffer.from(h, 'base64url'));
  const payload = JSON.parse(Buffer.from(p, 'base64url'));
  assert.deepStrictEqual(header, { alg: 'ES256', kid: 'KEY123', typ: 'JWT' });
  assert.strictEqual(payload.iss, 'issuer-uuid');
  assert.strictEqual(payload.aud, 'appstoreconnect-v1');
  assert.strictEqual(payload.bid, BUNDLE);
  assert.ok(payload.exp > payload.iat && payload.exp - payload.iat <= 3600,
    'Apple refuses a token that lives longer than an hour');
  const signature = Buffer.from(s, 'base64url');
  assert.strictEqual(signature.length, 64, 'JWS wants raw r||s, not DER');
  const ok = await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' },
    keys.publicKey, signature, new TextEncoder().encode(`${h}.${p}`));
  assert.ok(ok, 'signature does not verify against the public key');
});

await test('a key pasted with literal \\n sequences still signs', async () => {
  const { env } = makeEnv({ APPLE_IAP_PRIVATE_KEY: PEM.replace(/\n/g, '\\n') });
  const token = await appStoreToken(env);
  assert.strictEqual(token.split('.').length, 3);
});

await test('a key that is not a key is configuration, not a refusal', async () => {
  const { env } = makeEnv({ APPLE_IAP_PRIVATE_KEY: 'not a key' });
  const calls = mockApple(() => [200, statusBody()]);
  const { status } = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(status, 503);
  assert.strictEqual(calls.length, 0);
});

await test('decodeJws reads the payload and survives garbage', async () => {
  assert.deepStrictEqual(decodeJws(jws({ a: 1 })), { a: 1 });
  assert.strictEqual(decodeJws('nonsense'), null);
});

console.log('\n/v1/apple — receipt, verifyReceipt (the binary in review)');

const IS_PROD_RECEIPT = (url) => url === 'https://buy.itunes.apple.com/verifyReceipt';
const IS_SANDBOX_RECEIPT = (url) => url === 'https://sandbox.itunes.apple.com/verifyReceipt';

await test('an active receipt becomes a licence, in the reply shape the old binary reads', async () => {
  const { env, db } = makeEnv();
  const calls = mockApple(() => [200, receiptBody()]);
  const { status, body } = await exchange(env, { receipt: 'BASE64RECEIPT' });
  assert.strictEqual(status, 200);
  assert.match(body.licence_key, /^DAWN-/, 'the binary in review reads only licence_key');
  assert.strictEqual(body.status, 'active');
  assert.ok(body.expires_at);
  assert.strictEqual(db.apple.get('2000000111').licence_key, body.licence_key);

  assert.ok(IS_PROD_RECEIPT(calls[0].url));
  const sent = JSON.parse(calls[0].init.body);
  assert.deepStrictEqual(sent, { 'receipt-data': 'BASE64RECEIPT',
    password: 'shared-secret', 'exclude-old-transactions': true });
});

await test('status 21007 retries sandbox, where App Review receipts live', async () => {
  const { env } = makeEnv();
  const calls = mockApple((url) => IS_PROD_RECEIPT(url)
    ? [200, { status: 21007 }] : [200, receiptBody()]);
  const { status, body } = await exchange(env, { receipt: 'SANDBOXRECEIPT' });
  assert.strictEqual(status, 200);
  assert.ok(body.licence_key);
  assert.strictEqual(calls.length, 2);
  assert.ok(IS_SANDBOX_RECEIPT(calls[1].url));
});

await test('a production receipt never asks sandbox (positive control)', async () => {
  const { env } = makeEnv();
  const calls = mockApple(() => [200, receiptBody()]);
  await exchange(env, { receipt: 'R' });
  assert.strictEqual(calls.length, 1);
});

await test('a receipt with only expired transactions is refused, and mints nothing', async () => {
  const { env, db } = makeEnv();
  const latest = Date.now() - 10 * DAY;
  mockApple(() => [200, receiptBody({ transactions: [
    { product_id: PRODUCT, original_transaction_id: '2000000111',
      expires_date_ms: String(latest - 30 * DAY) },
    { product_id: PRODUCT, original_transaction_id: '2000000111',
      expires_date_ms: String(latest) },
  ] })]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.error, 'not_subscribed');
  assert.strictEqual(body.status, 'expired');
  assert.strictEqual(body.expires_at, new Date(latest).toISOString(),
    'reports the latest expiry, not the first');
  assert.strictEqual(db.licences.size, 0);
});

await test('a lapsed receipt in billing grace is still entitled', async () => {
  const { env } = makeEnv();
  const grace = Date.now() + 5 * DAY;
  mockApple(() => [200, receiptBody({
    transactions: [{ product_id: PRODUCT, original_transaction_id: '2000000111',
                     expires_date_ms: String(Date.now() - DAY) }],
    renewals: [{ original_transaction_id: '2000000111', product_id: PRODUCT,
                 is_in_billing_retry_period: '1',
                 grace_period_expires_date_ms: String(grace) }],
  })]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 200);
  assert.strictEqual(body.status, 'grace');
  assert.strictEqual(body.expires_at, new Date(grace).toISOString());
});

await test('billing retry without grace is refused as billing_retry', async () => {
  const { env } = makeEnv();
  mockApple(() => [200, receiptBody({
    transactions: [{ product_id: PRODUCT, original_transaction_id: '2000000111',
                     expires_date_ms: String(Date.now() - DAY) }],
    renewals: [{ original_transaction_id: '2000000111', is_in_billing_retry_period: '1' }],
  })]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.status, 'billing_retry');
});

await test('a refunded transaction is refused even before its expiry date', async () => {
  const { env } = makeEnv();
  mockApple(() => [200, receiptBody({ transactions: [
    { product_id: PRODUCT, original_transaction_id: '2000000111',
      expires_date_ms: String(Date.now() + 20 * DAY),
      cancellation_date_ms: String(Date.now() - DAY) },
  ] })]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.status, 'revoked');
});

await test('a receipt for another app is refused', async () => {
  const { env } = makeEnv();
  mockApple(() => [200, receiptBody({ bundleId: 'com.example.other' })]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.error, 'wrong_bundle');
});

await test('a receipt holding only another product is refused', async () => {
  const { env } = makeEnv();
  mockApple(() => [200, receiptBody({ transactions: [
    { product_id: 'com.spencerfields.dawnlist.yearly', original_transaction_id: '1',
      expires_date_ms: String(Date.now() + DAY) },
  ] })]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.error, 'wrong_product');
});

await test('without APPLE_SHARED_SECRET the receipt path is 503 and Apple is not asked', async () => {
  const { env } = makeEnv({ APPLE_SHARED_SECRET: undefined });
  const calls = mockApple(() => [200, receiptBody()]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 503);
  assert.strictEqual(body.error, 'apple_not_configured');
  assert.strictEqual(calls.length, 0);
});

await test('the receipt path does not need the In-App Purchase key', async () => {
  const { env } = makeEnv({ APPLE_IAP_PRIVATE_KEY: undefined, APPLE_IAP_KEY_ID: undefined,
                            APPLE_IAP_ISSUER_ID: undefined });
  mockApple(() => [200, receiptBody()]);
  assert.strictEqual((await exchange(env, { receipt: 'R' })).status, 200);
});

await test('a wrong shared secret (21004) is 502, never a refusal', async () => {
  const { env } = makeEnv();
  mockApple(() => [200, { status: 21004 }]);
  const { status } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 502);
});

await test('a malformed receipt (21002) is a refusal', async () => {
  const { env } = makeEnv();
  mockApple(() => [200, { status: 21002 }]);
  const { status, body } = await exchange(env, { receipt: 'R' });
  assert.strictEqual(status, 403);
  assert.strictEqual(body.error, 'unknown_transaction');
});

console.log('\n/v1/apple — one licence whichever shape asked');

await test('an old-binary receipt and a new-build transaction id share the licence', async () => {
  const { env, db } = makeEnv();
  mockApple((url) => url.includes('verifyReceipt') ? [200, receiptBody()] : [200, statusBody()]);
  const fromReceipt = await exchange(env, { receipt: 'R' });
  const fromTransaction = await exchange(env, { originalTransactionId: '2000000111' });
  assert.strictEqual(fromReceipt.body.licence_key, fromTransaction.body.licence_key,
    'updating the app must not mint a second licence for one purchase');
  assert.strictEqual(db.licences.size, 1);
});

await test('neither shape is a bad request', async () => {
  const { env } = makeEnv();
  const { status } = await exchange(env, { something: 'else' });
  assert.strictEqual(status, 400);
});

await test('the route is POST only', async () => {
  const { env } = makeEnv();
  const res = await worker.fetch(new Request('https://w.example/v1/apple'), env, {});
  assert.strictEqual(res.status, 404);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
