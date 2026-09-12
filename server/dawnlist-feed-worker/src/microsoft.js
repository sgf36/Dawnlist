/**
 * `/v1/microsoft` — a Microsoft Store subscription, confirmed WITH MICROSOFT,
 * as a licence.
 *
 * WHY THE APP'S OWN ANSWER IS NOT ENOUGH
 * --------------------------------------
 * `Windows.Services.Store` will happily tell the app it holds the add-on, and
 * that is good enough to decide what the WINDOW shows. It is not evidence: a
 * patched build can claim anything, and the feed is metered per licence. So
 * the client sends a Store ID key — signed by the Store, not by us — and this
 * exchanges it with Microsoft's collections API. The app's opinion never
 * reaches here.
 *
 * TWO CALLS, NOT ONE, AND THAT IS MICROSOFT'S DESIGN
 * --------------------------------------------------
 * A Store ID key cannot be minted by the client alone. It needs a *service
 * ticket* from us first, so the sequence is:
 *
 *   1. app  -> GET  /v1/microsoft/ticket      (we mint an Azure AD token)
 *   2. app  ->      GetCustomerCollectionsIdAsync(ticket)   -> Store ID key
 *   3. app  -> POST /v1/microsoft             (we verify the key)
 *
 * The extra round trip is not ours to remove. It is why the client function
 * takes a `service_ticket` argument rather than pretending it can make a key
 * on its own.
 *
 * TWO AUDIENCES FROM ONE REGISTRATION. The ticket is minted for
 * `.../b2b/keys/create/collections` and the query for `onestore.microsoft.com`.
 * Same client id and secret, different `scope`. Using one where the other
 * belongs returns a token that is valid and refused, which reads like a
 * permissions problem and is not one.
 *
 * THE CREDENTIAL HERE IS SAFE IN A WORKER, AND THE APPLE ONE WAS NOT. This
 * registration can read Store collections and nothing else — it cannot touch
 * the listing, the pricing or a build. Apple offers no such grain, which is
 * why `tools/asc_offer_codes.py` runs on a laptop and this runs in Cloudflare.
 */
import { capsFor } from './plans.js';
import { newLicenceKey } from './paddle.js';

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json; charset=utf-8' },
  });

/** Scope for the token the CLIENT uses to mint a Store ID key. */
const TICKET_SCOPE = 'https://onestore.microsoft.com/b2b/keys/create/collections/.default';

/** Scope for the token WE use to query what a customer owns. */
const QUERY_SCOPE = 'https://onestore.microsoft.com/.default';

const COLLECTIONS_URL = 'https://collections.mp.microsoft.com/v6.0/collections/query';

const SECRETS = ['MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_PRODUCT_ID'];

const notConfigured = (missing) => json({
  error: 'microsoft_not_configured',
  message: `Microsoft verification is not configured (${missing.join(', ')})`,
}, 503);

function missingFor(env, names) {
  return names.filter((name) => !env[name]);
}

/**
 * An Azure AD token for one audience.
 *
 * Minted per request and never cached. A token lives an hour, and caching one
 * in a Worker means holding a bearer credential in memory shared across every
 * request that isolate serves — for a saving of one round trip on a path that
 * already makes two.
 */
export async function azureToken(env, scope, { fetcher = fetch } = {}) {
  const body = new URLSearchParams({
    grant_type: 'client_credentials',
    client_id: env.MS_CLIENT_ID,
    client_secret: env.MS_CLIENT_SECRET,
    scope,
  });
  const res = await fetcher(
    `https://login.microsoftonline.com/${env.MS_TENANT_ID}/oauth2/v2.0/token`,
    { method: 'POST', body,
      headers: { 'content-type': 'application/x-www-form-urlencoded' } });
  if (!res.ok) {
    // Azure puts the reason in the body and it is worth keeping:
    // AADSTS7000215 is a wrong secret, AADSTS700016 a wrong client id, and
    // they are indistinguishable from the status alone.
    let detail = '';
    try { detail = (await res.json()).error_description || ''; } catch { /* ignore */ }
    throw new Error(`azure_token_${res.status}: ${String(detail).slice(0, 200)}`);
  }
  const { access_token: token } = await res.json();
  if (!token) throw new Error('azure_token_empty');
  return token;
}

/**
 * Step 1: the service ticket the app needs before it can prove anything.
 *
 * Handed out WITHOUT authentication, deliberately. It is not a capability: on
 * its own it proves nothing and grants nothing, and it is only useful to
 * somebody who already has a Microsoft Store account with our add-on in it.
 * Gating it behind a licence would be circular — the licence is what the app
 * is trying to obtain.
 */
export async function handleTicket(env, { fetcher = fetch } = {}) {
  const missing = missingFor(env, SECRETS);
  if (missing.length) return notConfigured(missing);
  try {
    return json({ ticket: await azureToken(env, TICKET_SCOPE, { fetcher }) });
  } catch (err) {
    // 502 and never 403: this is our configuration failing, not the customer
    // being refused, and the app reads 5xx as "could not ask" rather than as
    // "you did not pay".
    return json({ error: 'azure_unreachable',
                  message: String(err.message || err).slice(0, 200) }, 502);
  }
}

/** What Microsoft calls a subscription that is still running. */
const ENTITLED = new Set(['Active', 'ActivePendingCancellation']);

/**
 * Step 3: the Store ID key, exchanged for a licence.
 */
export async function handleMicrosoft(request, env, { fetcher = fetch } = {}) {
  const missing = missingFor(env, SECRETS);
  if (missing.length) return notConfigured(missing);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: 'bad_request', message: 'Send collectionsKey' }, 400);
  }
  const key = String(body?.collectionsKey || '').trim();
  if (!key) return json({ error: 'bad_request', message: 'Send collectionsKey' }, 400);

  let token;
  try {
    token = await azureToken(env, QUERY_SCOPE, { fetcher });
  } catch (err) {
    return json({ error: 'azure_unreachable',
                  message: String(err.message || err).slice(0, 200) }, 502);
  }

  let payload;
  try {
    const res = await fetcher(COLLECTIONS_URL, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`,
                 'content-type': 'application/json' },
      body: JSON.stringify({
        maxPageSize: 100,
        beneficiaries: [{ identitytype: 'b2b', identityValue: key,
                          localTicketReference: '' }],
        productSkuIds: [{ productId: env.MS_PRODUCT_ID }],
      }),
    });
    if (res.status === 401 || res.status === 403) {
      // THE FAILURE WORTH NAMING. A token that mints and a query that is
      // refused almost always means the app registration was never added in
      // Partner Center under User management -> Azure AD applications. It
      // otherwise reads as "this customer owns nothing", which sends somebody
      // to look at the app instead of at a permission.
      return json({ error: 'microsoft_auth_failed',
                    message: 'Microsoft refused the collections query — is the app '
                      + 'registration added in Partner Center?' }, 502);
    }
    // A 400 IS AN ANSWER, NOT AN OUTAGE. Microsoft looked at the key and said
    // it was not one. Reporting that as unreachable sends somebody to check
    // Cloudflare and their own connection for a fault in the CLIENT — measured
    // 2026-09-12, when a deliberately junk key came back as 400 and this
    // surfaced it as `microsoft_unreachable`.
    //
    // 400 to the caller rather than 5xx, because the app reads 5xx as "could
    // not ask" and would sit on grace waiting for a network that is fine.
    if (res.status === 400) {
      return json({ error: 'bad_collections_key',
                    message: 'Microsoft did not recognise that Store ID key. It is '
                      + 'minted by GetCustomerCollectionsIdAsync and expires; '
                      + 'ask /v1/microsoft/ticket for a fresh ticket and try again.' },
                  400);
    }
    if (!res.ok) return json({ error: 'microsoft_unreachable', status: res.status }, 502);
    payload = await res.json();
  } catch {
    return json({ error: 'microsoft_unreachable' }, 502);
  }

  const items = Array.isArray(payload?.items) ? payload.items : [];
  const mine = items.filter((item) => item?.productId === env.MS_PRODUCT_ID);
  if (!mine.length) return json({ error: 'not_subscribed' }, 403);

  // The most generous row wins when a customer has more than one — a
  // resubscription leaves the old row in place, and refusing on the stale one
  // would lock out somebody who has just paid again.
  const live = mine.find((item) => ENTITLED.has(item?.status)) || mine[0];
  const userId = String(live?.beneficiary?.identityValue || live?.id || key).slice(0, 200);

  if (!ENTITLED.has(live?.status)) {
    return settleMicrosoft(env, {
      userId, entitled: false, status: String(live?.status || 'Unknown'),
      expiresAt: live?.endDate || null, productId: env.MS_PRODUCT_ID,
    });
  }
  return settleMicrosoft(env, {
    userId, entitled: true, status: String(live.status),
    expiresAt: live.endDate || null, productId: env.MS_PRODUCT_ID,
  });
}

/**
 * One Microsoft customer maps to exactly one licence, whichever call asked.
 *
 * Written to mirror `settle` in apple.js rather than to be clever: the two
 * stores differ in how a purchase is proved and in nothing else, and a reader
 * comparing them should find the difference in one place.
 */
export async function settleMicrosoft(env, { userId, entitled, status, expiresAt,
                                             productId }) {
  const now = new Date().toISOString();

  if (!entitled) {
    const mapped = await env.DB.prepare(
      'SELECT licence_key FROM microsoft_transactions WHERE user_id = ?1'
    ).bind(userId).first();
    if (mapped) {
      await env.DB.prepare(
        `UPDATE microsoft_transactions SET status = ?1, expires_at = ?2, updated_at = ?3
          WHERE user_id = ?4`
      ).bind(status, expiresAt, now, userId).run();
      // Only the licence this purchase created. A Paddle or Apple licence is
      // never in this table, so a lapsed Store subscription cannot switch off
      // something bought elsewhere.
      await env.DB.prepare(
        "UPDATE licences SET status = 'expired', expires_at = ?1 WHERE licence_key = ?2"
      ).bind(expiresAt, mapped.licence_key).run();
    }
    return json({ error: 'not_subscribed', status, expires_at: expiresAt }, 403);
  }

  await env.DB.prepare(
    `INSERT INTO microsoft_transactions (user_id, licence_key, product_id, status,
                                         expires_at, created_at, updated_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)
     ON CONFLICT(user_id) DO UPDATE SET
       product_id = excluded.product_id,
       status     = excluded.status,
       expires_at = excluded.expires_at,
       updated_at = excluded.updated_at`
  ).bind(userId, newLicenceKey(), productId, status, expiresAt, now).run();

  const { licence_key: licenceKey } = await env.DB.prepare(
    'SELECT licence_key FROM microsoft_transactions WHERE user_id = ?1'
  ).bind(userId).first();

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

  return json({ ok: true, licence_key: licenceKey, expires_at: expiresAt, status });
}
