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

  const parts = Object.fromEntries(
    header.split(';').map((p) => p.split('=').map((s) => s.trim()))
  );
  const ts = parts.ts;
  const h1 = parts.h1;
  if (!ts || !h1) return { ok: false, reason: 'malformed Paddle-Signature' };

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

  return timingSafeEqual(computed, h1)
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

export async function handlePaddleWebhook(request, env) {
  const raw = await request.text();
  const check = await verifySignature(
    raw, request.headers.get('Paddle-Signature'), env.PADDLE_WEBHOOK_SECRET
  );
  if (!check.ok) {
    // 401 so Paddle retries and the delivery log shows the real reason.
    return new Response(JSON.stringify({ error: 'bad_signature', reason: check.reason }),
      { status: 401, headers: { 'content-type': 'application/json' } });
  }

  let event;
  try {
    event = JSON.parse(raw);
  } catch {
    return new Response('{"error":"bad_json"}', { status: 400 });
  }

  const type = event.event_type;
  const eventId = event.event_id;

  // Idempotency. Paddle retries on any non-2xx, and a retry that issues a
  // SECOND licence for one payment is worse than a missed one: the customer
  // has two keys, the meter is split across them, and nothing looks wrong.
  if (eventId) {
    const seen = await env.DB.prepare(
      'SELECT 1 FROM webhook_events WHERE event_id = ?1'
    ).bind(eventId).first();
    if (seen) {
      return new Response(JSON.stringify({ ok: true, deduplicated: true }),
        { status: 200, headers: { 'content-type': 'application/json' } });
    }
  }

  let result = { ok: true, action: 'ignored', event_type: type };

  if (GRANTING.has(type)) {
    const subscriptionId = event.data?.subscription_id || event.data?.id || null;
    const existing = subscriptionId
      ? await env.DB.prepare(
          'SELECT licence_key FROM licences WHERE paddle_subscription_id = ?1'
        ).bind(subscriptionId).first()
      : null;

    if (existing) {
      // Reactivating an existing subscriber must not mint a second key.
      await env.DB.prepare(
        "UPDATE licences SET status = 'active' WHERE licence_key = ?1"
      ).bind(existing.licence_key).run();
      result = { ok: true, action: 'reactivated' };
    } else {
      const key = newLicenceKey();
      // The caps the customer actually paid for. Before this, every licence
      // fell back to the Worker's anti-abuse default and paying more bought
      // nothing at all.
      const chosen = planForEvent(env, event);
      const caps = capsFor(chosen.plan);
      await env.DB.prepare(
        `INSERT INTO licences (licence_key, tier, status, paddle_subscription_id,
                               plan, plan_unmatched,
                               max_postings_per_day, max_refreshes_per_day,
                               max_saved_queries)
         VALUES (?1, ?2, 'active', ?3, ?4, ?5, ?6, ?7, ?8)`
      ).bind(key, tierFor(event), subscriptionId, caps.plan,
             chosen.matched ? 0 : 1,
             caps.max_postings_per_day, caps.max_refreshes_per_day,
             caps.max_saved_queries).run();
      if (!chosen.matched) {
        // Counts and ids only, never the payload. A customer on the fallback
        // may be on the wrong caps and this is the only trace left.
        console.warn('paddle: no price id matched a plan; used fallback',
                     { plan: caps.plan, subscription: subscriptionId ? 'yes' : 'no' });
      }
      // The key is NOT returned in the response: the webhook response goes to
      // Paddle, not to the customer. Delivery is a separate, deliberate step.
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
        // Do NOT fall back here. On the issue path the fallback is the least
        // bad option because a licence must exist; on an update it would
        // DOWNGRADE a paying customer to Standard because a price id was not
        // configured. Leave the caps alone and say so.
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

  if (eventId) {
    await env.DB.prepare(
      `INSERT INTO webhook_events (event_id, event_type, action, received_at)
       VALUES (?1, ?2, ?3, datetime('now'))
       ON CONFLICT(event_id) DO NOTHING`
    ).bind(eventId, type, result.action).run();
  }

  return new Response(JSON.stringify(result),
    { status: 200, headers: { 'content-type': 'application/json' } });
}
