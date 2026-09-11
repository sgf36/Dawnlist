/**
 * Paddle webhook → licence issuance.
 *
 * Lives in the feed Worker rather than its own, because it writes to the same
 * D1 database the feed reads licences from. Two Workers sharing one database is
 * two things to deploy and one more place for the binding to drift.
 *
 * READ server/WEBHOOK-RUNBOOK.md BEFORE CHANGING ANYTHING HERE. The sibling
 * product's webhook broke four times and looked like a different fault each
 * time. The three that matter:
 *
 *   1. `wrangler secret put PADDLE_WEBHOOK_SECRET` takes the secret's NAME, not
 *      its value. Typing the value creates a secret whose *name* is the signing
 *      secret and leaves the real one unset.
 *   2. Never verify by guessing. Replay a no-op event and confirm a 200.
 *      "Wrangler said success" proves a value was stored, not the right one.
 *   3. A Paddle secret embeds its own destination id. If several destinations
 *      point at this URL, the correct secret is the one matching THIS
 *      destination — the most convincingly named one may be dead.
 */
import { planForEvent, capsFor } from './plans.js';
import { customerDetails, licenceEmail, sendEmail } from './email.js';

const SIGNATURE_TOLERANCE_SECONDS = 5 * 60;

/** Events that grant or revoke access. Anything else is acknowledged and ignored. */
const GRANTING = new Set([
  'transaction.completed',
  'subscription.created',
  'subscription.activated',
]);
const REVOKING = new Set([
  'subscription.canceled',
  'subscription.paused',
]);
/**
 * A plan change. This is the event that makes "pay more for a higher cap"
 * actually work: without it an upgrade takes the customer's money and leaves
 * their caps exactly where they were.
 *
 * It fires for many reasons besides a plan change — a new card, a billing
 * address, a scheduled change being applied — so the handler re-derives the
 * plan from the items and writes only when it differs. Treating every one of
 * these as a plan change would rewrite caps on a card update, silently
 * resetting a licence that support had raised by hand.
 */
const UPDATING = new Set([
  'subscription.updated',
]);

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/**
 * Verify Paddle's signature over the RAW body.
 *
 * The raw text matters: re-serialising the parsed JSON produces different bytes
 * and the signature will never match, which reads as a wrong secret and sends
 * you looking in the wrong place.
 */
export async function verifySignature(rawBody, header, secret) {
  if (!header) return { ok: false, reason: 'no Paddle-Signature header' };
  if (!secret) return { ok: false, reason: 'PADDLE_WEBHOOK_SECRET is not set' };

  // Every h1 is kept, not just one. While a secret is being rotated Paddle
  // signs with the old and the new secret and sends an h1 for each; reading
  // the header into an object kept only the last, so a delivery signed with
  // the secret this Worker holds was refused whenever that h1 came first.
  let ts = null;
  const candidates = [];
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq === -1) continue;
    const name = part.slice(0, eq).trim();
    const value = part.slice(eq + 1).trim();
    if (name === 'ts') ts = value;
    else if (name === 'h1' && value) candidates.push(value);
  }
  if (!ts || candidates.length === 0) return { ok: false, reason: 'malformed Paddle-Signature' };

  const age = Math.abs(Math.floor(Date.now() / 1000) - Number(ts));
  if (!Number.isFinite(age) || age > SIGNATURE_TOLERANCE_SECONDS) {
    // A replayed old event is refused, but say WHICH check failed — "invalid
    // signature" for a stale timestamp sends you hunting the secret.
    return { ok: false, reason: `timestamp is ${age}s old` };
  }

  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const mac = await crypto.subtle.sign(
    'HMAC', key, new TextEncoder().encode(`${ts}:${rawBody}`)
  );
  const computed = [...new Uint8Array(mac)]
    .map((b) => b.toString(16).padStart(2, '0')).join('');

  // Compared against every candidate even after a match, so the time taken
  // does not reveal which position matched.
  let matched = false;
  for (const h1 of candidates) matched = timingSafeEqual(computed, h1) || matched;
  return matched
    ? { ok: true }
    : { ok: false, reason: 'signature mismatch — wrong secret for this destination' };
}

/** A licence key. Random, not derived from anything about the customer. */
export function newLicenceKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  const body = [...bytes].map((b) => b.toString(36).padStart(2, '0')).join('')
    .slice(0, 32).toUpperCase();
  return `DAWN-${body.match(/.{1,8}/g).join('-')}`;
}

function tierFor(event) {
  // A one-time purchase is the BYO-keys tier; a subscription is managed.
  // Getting this backwards would give a one-time buyer metered feed access.
  if (event.event_type === 'transaction.completed') {
    const items = event.data?.items || [];
    const recurring = items.some((i) => i.price?.billing_cycle);
    return recurring ? 'managed' : 'byo';
  }
  return 'managed';
}

/** The price ids an event names. Identifiers only, so safe to log. */
function priceIdsIn(event) {
  return (event.data?.items || [])
    .map((item) => item?.price?.id || item?.price_id)
    .filter((id) => typeof id === 'string')
    .slice(0, 10);
}

const reply = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

export async function handlePaddleWebhook(request, env, ctx) {
  const raw = await request.text();
  const check = await verifySignature(
    raw, request.headers.get('Paddle-Signature'), env.PADDLE_WEBHOOK_SECRET
  );
  if (!check.ok) {
    // 401 so Paddle retries. The REASON goes to the Worker's log, not the
    // response: anybody can post to this URL, and telling them whether it was
    // the timestamp or the signature that failed tells a forger which one to
    // work on. Whoever diagnoses a failed delivery reads it in the Worker's
    // observability logs, where "timestamp" still points away from the secret.
    console.warn('paddle: signature refused', { reason: check.reason });
    return reply({ error: 'bad_signature' }, 401);
  }

  let event;
  try {
    event = JSON.parse(raw);
  } catch {
    return new Response('{"error":"bad_json"}', { status: 400 });
  }

  const type = event.event_type;
  const eventId = event.event_id;

  // Idempotency, CLAIMED before anything is done. Paddle retries on any
  // non-2xx, and a retry that issues a SECOND licence for one payment is worse
  // than a missed one: the customer has two keys, the meter is split across
  // them, and nothing looks wrong. This used to be a read here and a write at
  // the end, so two deliveries of one event arriving together both read
  // "unseen" and both issued. The conditional insert lets exactly one of them
  // through.
  if (eventId) {
    const claimed = await env.DB.prepare(
      `INSERT INTO webhook_events (event_id, event_type, action, received_at)
       VALUES (?1, ?2, 'processing', datetime('now'))
       ON CONFLICT(event_id) DO NOTHING
       RETURNING event_id`
    ).bind(eventId, String(type || 'unknown')).first();
    if (!claimed) return reply({ ok: true, deduplicated: true });
  }

  let result;
  try {
    result = await applyEvent(env, ctx, event);
  } catch (err) {
    // Release the claim so Paddle's retry is processed rather than answered as
    // a duplicate of an event that never finished. Retrying is safe: a licence
    // already written is found by the unique subscription index, not issued
    // again.
    if (eventId) {
      try {
        await env.DB.prepare('DELETE FROM webhook_events WHERE event_id = ?1')
          .bind(eventId).run();
      } catch {
        console.error('paddle: could not release a failed event', { type });
      }
    }
    throw err;
  }

  if (eventId) {
    await env.DB.prepare('UPDATE webhook_events SET action = ?2 WHERE event_id = ?1')
      .bind(eventId, result.action).run();
  }
  return reply(result);
}

async function applyEvent(env, ctx, event) {
  const type = event.event_type;
  let result = { ok: true, action: 'ignored', event_type: type };

  if (GRANTING.has(type)) {
    const subscriptionId = event.data?.subscription_id || event.data?.id || null;
    const existing = subscriptionId
      ? await env.DB.prepare(
          'SELECT licence_key FROM licences WHERE paddle_subscription_id = ?1'
        ).bind(subscriptionId).first()
      : null;

    const chosen = planForEvent(env, event);
    if (existing) {
      // Reactivating an existing subscriber must not mint a second key. Not
      // gated on the price: the licence already exists, however it came to.
      await env.DB.prepare(
        "UPDATE licences SET status = 'active' WHERE licence_key = ?1"
      ).bind(existing.licence_key).run();
      result = { ok: true, action: 'reactivated' };
    } else if (!chosen.matched) {
      // An unrecognised price issues NOTHING. The fallback used to issue a
      // Standard licence and flag it, which handed a working key and a welcome
      // email to whoever bought something this Worker does not sell — another
      // product's price on the shared Paddle account, a test price, an id
      // missing from configuration. A licence wrongly withheld can be granted
      // by hand with a code minted in /admin; one wrongly issued is a live
      // credential already sitting in somebody's inbox.
      console.warn('paddle: price matched no plan; nothing issued',
                   { prices: priceIdsIn(event) });
      result = { ok: true, action: 'unmatched_product' };
    } else {
      const key = newLicenceKey();
      // The caps the customer actually paid for. Before this, every licence
      // fell back to the Worker's anti-abuse default and paying more bought
      // nothing at all.
      const caps = capsFor(chosen.plan);
      // RETURNING says whether THIS request created the row. The read above
      // is not enough: subscription.created and transaction.completed for one
      // purchase are different events, so both pass the idempotency claim,
      // both read "no licence", and both reach here. The unique index on
      // paddle_subscription_id refuses the second, and nothing is returned.
      const created = await env.DB.prepare(
        `INSERT INTO licences (licence_key, tier, status, paddle_subscription_id,
                               plan, plan_unmatched,
                               max_postings_per_day, max_refreshes_per_day,
                               max_saved_queries)
         VALUES (?1, ?2, 'active', ?3, ?4, 0, ?5, ?6, ?7)
         ON CONFLICT DO NOTHING
         RETURNING licence_key`
      ).bind(key, tierFor(event), subscriptionId, caps.plan,
             caps.max_postings_per_day, caps.max_refreshes_per_day,
             caps.max_saved_queries).first();

      if (!created) {
        // Another event for this purchase issued it first, and sent the email.
        // Sending one here too would put a key in the inbox that does not
        // exist in the database.
        return { ok: true, action: 'already_issued' };
      }

      // The key is NOT returned in the response: the webhook response goes to
      // Paddle, not to the customer. Delivery is the separate step below.
      //
      // It runs AFTER the licence is committed and OUTSIDE the response, via
      // waitUntil. Two reasons, and both are about what a retry would cost:
      // Paddle retries any non-2xx, so a slow or failing Resend call must not
      // turn a successful issue into a redelivery — and a redelivery that got
      // past the idempotency table would issue a second subscription for one
      // payment. A customer who does not receive the email can be sent it
      // again; a customer charged twice cannot be un-charged so easily.
      const deliver = (async () => {
        const who = await customerDetails(env, event);
        if (!who.email) {
          // Logged loudly and by cause. This is the failure that strands a
          // paying customer, and the only trace left will be this line.
          console.error('paddle: licence issued but NOT delivered — no email',
                        { source: who.source, subscription: subscriptionId ? 'yes' : 'no' });
          return;
        }
        const mail = licenceEmail({
          licenceKey: key,
          postingsPerDay: caps.max_postings_per_day,
          lang: who.lang,
        });
        const sent = await sendEmail(env, { to: who.email, ...mail });
        if (!sent.ok) {
          console.error('paddle: licence issued but email failed',
                        { error: sent.error, status: sent.status, lang: who.lang });
        } else {
          // Counts and ids only, never the address.
          console.log('paddle: licence delivered', { id: sent.id, lang: who.lang });
        }
      })();
      if (ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(deliver);
      else await deliver;

      result = { ok: true, action: 'issued' };
    }
  } else if (REVOKING.has(type)) {
    const subscriptionId = event.data?.id || event.data?.subscription_id;
    if (subscriptionId) {
      await env.DB.prepare(
        "UPDATE licences SET status = 'expired' WHERE paddle_subscription_id = ?1"
      ).bind(subscriptionId).run();
      result = { ok: true, action: 'revoked' };
    }
  } else if (UPDATING.has(type)) {
    const subscriptionId = event.data?.id || event.data?.subscription_id;
    const row = subscriptionId
      ? await env.DB.prepare(
          `SELECT licence_key, plan FROM licences WHERE paddle_subscription_id = ?1`
        ).bind(subscriptionId).first()
      : null;

    if (!row) {
      // An update for a subscription we never issued a licence for. Not an
      // error worth a retry — Paddle would redeliver forever — but it is worth
      // saying, because it means an earlier grant was missed.
      result = { ok: true, action: 'update_no_licence' };
    } else {
      const chosen = planForEvent(env, event);
      if (!chosen.matched) {
        // Do NOT fall back here. It would DOWNGRADE a paying customer to
        // Standard because a price id was not configured. Leave the caps alone
        // and say so.
        console.warn('paddle: subscription.updated matched no plan; caps unchanged',
                     { had: row.plan || 'unset' });
        result = { ok: true, action: 'update_unmatched_ignored' };
      } else if (chosen.plan === row.plan) {
        result = { ok: true, action: 'update_no_plan_change' };
      } else {
        const caps = capsFor(chosen.plan);
        // Read the previous plan BEFORE the update. Do not rely on the driver
        // handing back a detached row: if it returns a live reference, the
        // UPDATE below rewrites it and the "from" reported here becomes the
        // value it was changed TO — an audit line that says a licence moved
        // from global to global.
        const previous = row.plan || 'unset';
        await env.DB.prepare(
          `UPDATE licences
              SET plan = ?1, plan_unmatched = 0,
                  max_postings_per_day = ?2,
                  max_refreshes_per_day = ?3,
                  max_saved_queries = ?4
            WHERE licence_key = ?5`
        ).bind(caps.plan, caps.max_postings_per_day, caps.max_refreshes_per_day,
               caps.max_saved_queries, row.licence_key).run();
        // Today's usage is deliberately NOT reset. An upgrade raises the
        // ceiling, which immediately gives back headroom on the same day; a
        // downgrade lowers it, and a customer already above the new cap simply
        // has no headroom until tomorrow. Zeroing the meter on either would
        // make repeated plan changes a way to fetch without limit.
        result = { ok: true, action: 'plan_changed', from: previous, to: caps.plan };
      }
    }
  }

  return result;
}
