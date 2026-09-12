/**
 * `/v1/apple` — a Mac App Store subscription, confirmed WITH APPLE, as a licence.
 *
 * THE ROUTE DID NOT EXIST. The Mac App Store build posted its receipt here and
 * the router answered 404, which the client's `except Exception: return None`
 * turned into "the App Store could not confirm an active subscription". Every
 * Mac subscriber would have paid and reached no feed, told that they had not.
 *
 * TWO REQUEST SHAPES, AND BOTH ARE LOAD-BEARING
 * ---------------------------------------------
 *   {"receipt": "<base64>"}              the binary ALREADY IN APP REVIEW
 *   {"originalTransactionId": "123..."}  every build after it
 *
 * The receipt shape is verified with Apple's `verifyReceipt`, which Apple has
 * deprecated. It is used anyway because the binary in review sends a receipt,
 * reads only `licence_key` from the reply, and cannot be changed without a
 * resubmission — so a Worker deploy is the only thing that can make that build
 * work. Drop this path once no supported build sends a receipt.
 *
 * The transaction shape uses the App Store Server API, which is the supported
 * route and needs no shared secret inside a request body.
 *
 * Both end in `settle()`, so one original transaction id maps to exactly one
 * licence whichever shape asked: a customer who updates the app keeps the same
 * licence, its usage history and its caps.
 *
 * SANDBOX IS NOT A TEST-ONLY CONCERN. App Review buys with sandbox accounts
 * against the production build, so production is asked first and sandbox on
 * Apple's own "wrong environment" answer. Without the fallback the reviewer's
 * purchase is refused and the build is rejected for a broken purchase flow.
 *
 * EVERY "COULD NOT ASK" IS A 5xx, EVERY "APPLE SAID NO" IS A 403. The client
 * honours a grace period for the first and refuses at once for the second, so
 * reporting our own misconfiguration as a 403 would tell a paying customer they
 * are not subscribed.
 */
import { newLicenceKey } from './paddle.js';
import { capsFor } from './plans.js';

const SERVER_API = {
  Production: 'https://api.storekit.itunes.apple.com',
  Sandbox: 'https://api.storekit-sandbox.itunes.apple.com',
};
const VERIFY_RECEIPT = {
  Production: 'https://buy.itunes.apple.com/verifyReceipt',
  Sandbox: 'https://sandbox.itunes.apple.com/verifyReceipt',
};

/** verifyReceipt's "this is a sandbox receipt, ask sandbox" status. */
const SANDBOX_RECEIPT = 21007;

/**
 * App Store Server API subscription statuses. 4 is entitled because Apple is
 * still collecting a failed renewal inside the grace period the developer
 * chose to offer, and cutting the customer off there defeats that choice.
 */
const ENTITLED_STATUS = { 1: 'active', 4: 'grace' };
const REFUSED_STATUS = { 2: 'expired', 3: 'billing_retry', 5: 'revoked' };

/** What the licence row says once Apple has refused, by refusal. */
const LICENCE_STATUS = { expired: 'expired', billing_retry: 'suspended',
                         revoked: 'refunded' };

/**
 * The App Store Server API token lives five minutes. Apple allows sixty, but a
 * token is minted per request, so a longer life only widens what a leaked log
 * line would be good for.
 */
const TOKEN_SECONDS = 300;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json; charset=utf-8' },
  });

const notConfigured = (missing) => json({
  error: 'apple_not_configured',
  message: `Apple verification is not configured (${missing.join(', ')})`,
}, 503);

function missingFor(env, names) {
  return names.filter((name) => !env[name]);
}

// ---------------------------------------------------------------------------
// Encoding
// ---------------------------------------------------------------------------

function base64url(bytes) {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64url(text) {
  const padded = text.replace(/-/g, '+').replace(/_/g, '/')
    + '='.repeat((4 - (text.length % 4)) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

/**
 * The DER bytes of a PKCS#8 PEM.
 *
 * Literal "\n" sequences are accepted as line breaks because a key pasted at a
 * one-line secret prompt arrives that way, and failing to import it would read
 * as Apple being unreachable rather than as a paste problem.
 */
function pemToDer(pem) {
  const body = String(pem)
    .replace(/\\n/g, '\n')
    .replace(/-----(BEGIN|END) PRIVATE KEY-----/g, '')
    .replace(/\s+/g, '');
  return fromBase64url(body.replace(/\+/g, '-').replace(/\//g, '_')).buffer;
}

/**
 * The ES256 bearer token the App Store Server API requires.
 *
 * WebCrypto's ECDSA signature is already the raw r||s form JWS wants, so no
 * DER unwrapping is needed — the usual source of a signature Apple rejects
 * with a 401 that looks like a wrong key.
 */
export async function appStoreToken(env, nowMs = Date.now()) {
  const iat = Math.floor(nowMs / 1000);
  const encoder = new TextEncoder();
  const segment = (obj) => base64url(encoder.encode(JSON.stringify(obj)));
  const signingInput = `${segment({ alg: 'ES256', kid: env.APPLE_IAP_KEY_ID, typ: 'JWT' })}.`
    + segment({
      iss: env.APPLE_IAP_ISSUER_ID,
      iat,
      exp: iat + TOKEN_SECONDS,
      aud: 'appstoreconnect-v1',
      bid: env.APPLE_BUNDLE_ID,
    });
  const key = await crypto.subtle.importKey(
    'pkcs8', pemToDer(env.APPLE_IAP_PRIVATE_KEY),
    { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const signature = await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' }, key, encoder.encode(signingInput));
  return `${signingInput}.${base64url(new Uint8Array(signature))}`;
}

/**
 * The payload of an Apple JWS, WITHOUT checking its certificate chain.
 *
 * That is safe here and only here. These JWS strings arrive in the body of a
 * response this Worker fetched itself, from Apple's own API host, over TLS,
 * authenticated with our own key. TLS has already established that Apple sent
 * them, and no client ever supplies one — the app sends only an id — so there
 * is no attacker-controlled signed payload to forge. Chain verification is
 * required for a JWS relayed by someone else, such as the app itself or an App
 * Store Server Notification; if either is ever accepted, verify it there.
 */
export function decodeJws(jws) {
  try {
    const [, payload] = String(jws).split('.');
    return JSON.parse(new TextDecoder().decode(fromBase64url(payload)));
  } catch {
    return null;
  }
}

function iso(ms) {
  const n = Number(ms);
  return Number.isFinite(n) && n > 0 ? new Date(n).toISOString() : null;
}

// ---------------------------------------------------------------------------
// The licence, shared by both shapes
// ---------------------------------------------------------------------------

/**
 * Record what Apple said, and return the reply.
 *
 * THE MAPPING IS WRITTEN BEFORE THE LICENCE, deliberately and without a foreign
 * key. Two exchanges for one purchase can race — a launch and a restore, or two
 * Macs on one Apple ID. Writing the mapping first with ON CONFLICT means both
 * converge on whichever licence key won, and the licence insert is then an
 * upsert of that one key. Licence-first would let each racer mint its own
 * licence, splitting the customer's usage across two keys with nothing showing
 * it; a foreign key would force exactly that order.
 */
async function settle(env, { originalTransactionId, entitled, status, expiresAt,
                             environment }) {
  const now = new Date().toISOString();
  const id = String(originalTransactionId);

  if (!entitled) {
    // A refusal updates a licence that already exists and creates nothing: a
    // licence minted for a lapsed subscription is a licence nobody paid for.
    const mapped = await env.DB.prepare(
      'SELECT licence_key FROM apple_transactions WHERE original_transaction_id = ?1'
    ).bind(id).first();
    if (mapped) {
      await env.DB.prepare(
        `UPDATE apple_transactions SET status = ?1, expires_at = ?2, updated_at = ?3
          WHERE original_transaction_id = ?4`
      ).bind(status, expiresAt, now, id).run();
      // Only the licence this purchase created. A Paddle or code-granted
      // licence is never in this table, so a lapsed Apple subscription cannot
      // switch off something bought elsewhere.
      await env.DB.prepare(
        'UPDATE licences SET status = ?1, expires_at = ?2 WHERE licence_key = ?3'
      ).bind(LICENCE_STATUS[status] || 'expired', expiresAt, mapped.licence_key).run();
    }
    return json({ error: 'not_subscribed', status, expires_at: expiresAt,
                  original_transaction_id: id }, 403);
  }

  await env.DB.prepare(
    `INSERT INTO apple_transactions (original_transaction_id, licence_key,
                                     environment, status, expires_at,
                                     created_at, updated_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)
     ON CONFLICT(original_transaction_id) DO UPDATE SET
       environment = excluded.environment,
       status      = excluded.status,
       expires_at  = excluded.expires_at,
       updated_at  = excluded.updated_at`
  ).bind(id, newLicenceKey(), environment, status, expiresAt, now).run();

  const { licence_key: licenceKey } = await env.DB.prepare(
    'SELECT licence_key FROM apple_transactions WHERE original_transaction_id = ?1'
  ).bind(id).first();

  const caps = capsFor('standard');
  await env.DB.prepare(
    `INSERT INTO licences (licence_key, tier, status, plan,
                           max_postings_per_day, max_refreshes_per_day,
                           max_saved_queries, expires_at)
     VALUES (?1, 'managed', 'active', ?2, ?3, ?4, ?5, ?6)
     ON CONFLICT(licence_key) DO UPDATE SET
       status = 'active', expires_at = excluded.expires_at`
  ).bind(licenceKey, caps.plan, caps.max_postings_per_day,
         caps.max_refreshes_per_day, caps.max_saved_queries, expiresAt).run();

  // The id goes back so a build that asked with a receipt can ask with the
  // id from then on, and stop depending on the deprecated receipt endpoint.
  return json({ ok: true, licence_key: licenceKey, expires_at: expiresAt, status,
                original_transaction_id: id });
}

// ---------------------------------------------------------------------------
// Shape 2: {"originalTransactionId"} through the App Store Server API
// ---------------------------------------------------------------------------

const TRANSACTION_SECRETS = ['APPLE_IAP_KEY_ID', 'APPLE_IAP_ISSUER_ID',
  'APPLE_IAP_PRIVATE_KEY', 'APPLE_BUNDLE_ID', 'APPLE_PRODUCT_ID'];

async function byTransaction(env, transactionId) {
  const missing = missingFor(env, TRANSACTION_SECRETS);
  if (missing.length) return notConfigured(missing);
  if (!/^[0-9]{1,40}$/.test(transactionId)) {
    return json({ error: 'bad_transaction_id' }, 400);
  }

  let token;
  try {
    token = await appStoreToken(env);
  } catch {
    // A key that will not import is configuration, not a customer's fault.
    return notConfigured(['APPLE_IAP_PRIVATE_KEY is not a PKCS#8 P-256 key']);
  }

  let res;
  let environment;
  try {
    for (environment of ['Production', 'Sandbox']) {
      res = await fetch(
        `${SERVER_API[environment]}/inApps/v1/subscriptions/${transactionId}`,
        { headers: { authorization: `Bearer ${token}` } });
      // Apple answers 404 in production for a transaction that lives in
      // sandbox, which is where every App Review purchase lives.
      if (res.status !== 404) break;
    }
  } catch {
    return json({ error: 'apple_unreachable' }, 502);
  }

  if (res.status === 404 || res.status === 400) {
    return json({ error: 'unknown_transaction' }, 403);
  }
  if (res.status === 401) {
    return json({ error: 'apple_auth_failed',
                  message: 'Apple refused our In-App Purchase key' }, 502);
  }
  if (!res.ok) return json({ error: 'apple_unreachable', status: res.status }, 502);

  const body = await res.json();
  if (body.bundleId && body.bundleId !== env.APPLE_BUNDLE_ID) {
    return json({ error: 'wrong_bundle' }, 403);
  }

  const candidates = [];
  let wrongBundle = false;
  for (const group of body.data || []) {
    for (const item of group.lastTransactions || []) {
      const txn = decodeJws(item.signedTransactionInfo);
      if (!txn) continue;
      if (txn.bundleId !== env.APPLE_BUNDLE_ID) { wrongBundle = true; continue; }
      if (txn.productId !== env.APPLE_PRODUCT_ID) continue;
      candidates.push({ item, txn, renewal: decodeJws(item.signedRenewalInfo) || {} });
    }
  }
  if (!candidates.length) {
    return json({ error: wrongBundle ? 'wrong_bundle' : 'wrong_product' }, 403);
  }

  const pick = candidates.find((c) => String(c.txn.originalTransactionId) === transactionId)
    || candidates.find((c) => ENTITLED_STATUS[c.item.status])
    || candidates[0];
  const { item, txn, renewal } = pick;

  let status = txn.revocationDate ? 'revoked'
    : (ENTITLED_STATUS[item.status] || REFUSED_STATUS[item.status]);
  if (!status) {
    // A status Apple adds later is not a refusal we can stand behind.
    return json({ error: 'apple_unexpected', status: item.status }, 502);
  }
  const entitled = Boolean(ENTITLED_STATUS[item.status]) && !txn.revocationDate;
  let expiresMs = Number(txn.expiresDate) || 0;
  if (status === 'grace') {
    expiresMs = Math.max(expiresMs, Number(renewal.gracePeriodExpiresDate) || 0);
  }

  return settle(env, {
    originalTransactionId: txn.originalTransactionId || item.originalTransactionId,
    entitled, status, expiresAt: iso(expiresMs),
    environment: txn.environment || environment,
  });
}

// ---------------------------------------------------------------------------
// Shape 1: {"receipt"} through verifyReceipt, for the binary in review
// ---------------------------------------------------------------------------

const RECEIPT_SECRETS = ['APPLE_SHARED_SECRET', 'APPLE_BUNDLE_ID', 'APPLE_PRODUCT_ID'];

/**
 * verifyReceipt statuses that are Apple saying the RECEIPT is no good, as
 * opposed to Apple or our configuration failing. 21004 (wrong shared secret)
 * is deliberately absent: that is our fault and must not refuse a customer.
 */
const RECEIPT_REFUSED = new Set([21002, 21003, 21006, 21010]);

async function byReceipt(env, receipt) {
  const nowMs = Date.now();
  const missing = missingFor(env, RECEIPT_SECRETS);
  if (missing.length) return notConfigured(missing);
  if (typeof receipt !== 'string' || !receipt.trim()) {
    return json({ error: 'bad_receipt' }, 400);
  }

  let body;
  let environment;
  try {
    for (environment of ['Production', 'Sandbox']) {
      const res = await fetch(VERIFY_RECEIPT[environment], {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          'receipt-data': receipt,
          password: env.APPLE_SHARED_SECRET,
          // Only the latest renewal of each subscription, which is all the
          // decision needs and keeps a long-lived subscriber's reply small.
          'exclude-old-transactions': true,
        }),
      });
      if (!res.ok) return json({ error: 'apple_unreachable', status: res.status }, 502);
      body = await res.json();
      if (body.status !== SANDBOX_RECEIPT) break;
    }
  } catch {
    return json({ error: 'apple_unreachable' }, 502);
  }

  if (body.status !== 0) {
    if (RECEIPT_REFUSED.has(body.status)) {
      return json({ error: 'unknown_transaction', apple_status: body.status }, 403);
    }
    return json({ error: 'apple_unreachable', apple_status: body.status }, 502);
  }
  if (body.receipt?.bundle_id !== env.APPLE_BUNDLE_ID) {
    return json({ error: 'wrong_bundle' }, 403);
  }

  const ours = (body.latest_receipt_info || [])
    .filter((t) => t.product_id === env.APPLE_PRODUCT_ID);
  if (!ours.length) return json({ error: 'wrong_product' }, 403);

  const renewals = body.pending_renewal_info || [];
  const graceFor = (t) => {
    const renewal = renewals.find(
      (r) => String(r.original_transaction_id) === String(t.original_transaction_id));
    return Number(renewal?.grace_period_expires_date_ms) || 0;
  };
  const retrying = (t) => renewals.some(
    (r) => String(r.original_transaction_id) === String(t.original_transaction_id)
      && String(r.is_in_billing_retry_period) === '1');

  const unrevoked = ours.filter((t) => !t.cancellation_date_ms);
  const latest = (list) => list.reduce((a, b) =>
    (Number(b.expires_date_ms) || 0) > (Number(a.expires_date_ms) || 0) ? b : a);

  const active = unrevoked.filter((t) => Number(t.expires_date_ms) > nowMs);
  if (active.length) {
    const t = latest(active);
    return settle(env, {
      originalTransactionId: t.original_transaction_id, entitled: true,
      status: 'active', expiresAt: iso(t.expires_date_ms), environment,
    });
  }
  const inGrace = unrevoked.filter((t) => graceFor(t) > nowMs);
  if (inGrace.length) {
    const t = latest(inGrace);
    return settle(env, {
      originalTransactionId: t.original_transaction_id, entitled: true,
      status: 'grace', expiresAt: iso(graceFor(t)), environment,
    });
  }

  const t = latest(ours);
  const status = t.cancellation_date_ms ? 'revoked'
    : (retrying(t) ? 'billing_retry' : 'expired');
  return settle(env, {
    originalTransactionId: t.original_transaction_id, entitled: false,
    status, expiresAt: iso(t.expires_date_ms), environment,
  });
}

// ---------------------------------------------------------------------------

export async function handleApple(request, env) {
  let body;
  try { body = await request.json(); } catch { return json({ error: 'bad_json' }, 400); }

  if (body && body.originalTransactionId !== undefined) {
    return byTransaction(env, String(body.originalTransactionId).trim());
  }
  if (body && body.receipt !== undefined) {
    return byReceipt(env, body.receipt);
  }
  return json({ error: 'bad_request',
                message: 'Send originalTransactionId, or receipt' }, 400);
}
