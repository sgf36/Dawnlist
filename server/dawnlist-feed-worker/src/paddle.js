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
 *
 * The notification destination must be subscribed to every event named in the
 * sets below, or the change that event carries never arrives. The README lists
 * them and what each one does.
 */
import { planForEvent, capsFor } from './plans.js';
import { customerDetails, licenceEmail, sendEmail } from './email.js';

const SIGNATURE_TOLERANCE_SECONDS = 5 * 60;

/** Events that may ISSUE a licence, and restore one that exists. */
const GRANTING = new Set([
  'transaction.completed',
  'subscription.created',
  'subscription.activated',
]);
/**
 * Events that only move an existing licence's status. With no licence yet they
 * are still recorded, so a grant that arrives later honours them.
 */
const STATUS_ONLY = new Set([
  'subscription.canceled',
  'subscription.paused',
  'subscription.resumed',
  'subscription.past_due',
  'subscription.trialing',
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
/**
 * Refunds and chargebacks. `updated` matters as much as `created`: a refund
 * made in the dashboard is created awaiting approval and only becomes final
 * when adjustment.updated reports it approved.
 */
const ADJUSTING = new Set([
  'adjustment.created',
  'adjustment.updated',
]);

/**
 * What a Paddle subscription status means for access.
 *
 * `past_due` keeps access: Paddle is still retrying the payment, and switching
 * a customer off at the first failed renewal locks out somebody whose card is
 * about to go through. It is recorded in paddle_subscriptions.paddle_status so
 * support can see it.
 */
const ACCESS_FOR_STATUS = {
  active: 'active',
  trialing: 'active',
  past_due: 'active',
  canceled: 'expired',
  paused: 'expired',
};
/** What an event implies when its payload carries no recognised status. */
const ACCESS_FOR_EVENT = {
  'transaction.completed': 'active',
  'subscription.created': 'active',
  'subscription.activated': 'active',
  'subscription.resumed': 'active',
  'subscription.trialing': 'active',
  'subscription.past_due': 'active',
  'subscription.canceled': 'expired',
  'subscription.paused': 'expired',
};

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

/**
 * The subscription an event is about. A transaction names it separately from
 * its own id; a one-time purchase has none, and its transaction id stands in,
 * which is what the licence was always keyed by.
 */
function subscriptionIdOf(event) {
  const data = event.data || {};
  return event.event_type?.startsWith('transaction.')
    ? (data.subscription_id || data.id || null)
    : (data.id || data.subscription_id || null);
}

/**
 * The event's own time, as text that sorts in time order.
 *
 * Paddle sends RFC 3339 with microseconds ("…T10:18:49.621022Z"). Padding the
 * fraction to a fixed width makes string order equal time order, which is what
 * lets the "is this newer?" comparison sit inside a single SQL statement rather
 * than in a read followed by a write that another delivery could get between.
 */
export function occurredAt(event) {
  const raw = event.occurred_at;
  if (typeof raw !== 'string') return null;
  const exact = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z$/.exec(raw);
  if (exact) return `${exact[1]}.${(exact[2] || '').padEnd(9, '0')}Z`;
  const ms = Date.parse(raw);
  if (Number.isNaN(ms)) return null;
  const iso = new Date(ms).toISOString();
  return `${iso.slice(0, 19)}.${iso.slice(20, 23).padEnd(9, '0')}Z`;
}

function statusesFor(event) {
  const type = event.event_type;
  const reported = type.startsWith('subscription.') && typeof event.data?.status === 'string'
    ? event.data.status
    : null;
  return {
    paddleStatus: reported,
    access: ACCESS_FOR_STATUS[reported] ?? ACCESS_FOR_EVENT[type] ?? null,
  };
}

/**
 * Record an event against its subscription if it is at least as new as the
 * last one recorded. RETURNING says whether it was. An event with no time
 * cannot be ordered and is applied, which is how every event was treated
 * before this existed.
 */
const STATE_UPSERT = `
  INSERT INTO paddle_subscriptions
         (subscription_id, last_event_at, paddle_status, licence_status, updated_at)
  VALUES (?1, ?2, ?3, ?4, datetime('now'))
  ON CONFLICT(subscription_id) DO UPDATE SET
    last_event_at  = COALESCE(excluded.last_event_at, paddle_subscriptions.last_event_at),
    paddle_status  = COALESCE(excluded.paddle_status, paddle_subscriptions.paddle_status),
    licence_status = COALESCE(excluded.licence_status, paddle_subscriptions.licence_status),
    updated_at     = excluded.updated_at
  WHERE excluded.last_event_at IS NULL
     OR paddle_subscriptions.last_event_at IS NULL
     OR excluded.last_event_at >= paddle_subscriptions.last_event_at
  RETURNING subscription_id`;

/**
 * Copy the newest recorded access onto the licence.
 *
 * It copies from the table rather than from the event, so running it after a
 * STALE event is harmless — it re-applies what the newer event said. Two
 * statuses are never overridden by it: 'suspended' is support's decision, and
 * 'refunded' means the money went back — a later "active" from Paddle describes
 * the subscription, not whether this customer paid for the access.
 */
const LICENCE_SYNC = `
  UPDATE licences
     SET status = (SELECT licence_status FROM paddle_subscriptions WHERE subscription_id = ?1)
   WHERE paddle_subscription_id = ?1
     AND status NOT IN ('suspended', 'refunded')
     AND (SELECT licence_status FROM paddle_subscriptions WHERE subscription_id = ?1) IS NOT NULL`;

const LICENCE_BY_SUBSCRIPTION =
  'SELECT licence_key, status, plan FROM licences WHERE paddle_subscription_id = ?1';

const RECORD_TRANSACTION = `
  INSERT INTO paddle_transactions (transaction_id, subscription_id, recorded_at)
  VALUES (?1, ?2, datetime('now'))
  ON CONFLICT(transaction_id) DO NOTHING`;

/**
 * RETURNING says whether THIS request created the row. subscription.created and
 * transaction.completed for one purchase are different events, so both pass the
 * idempotency claim and both reach here; the unique index on
 * paddle_subscription_id refuses the second, and nothing is returned. The
 * status written is provisional — LICENCE_SYNC, in the same batch, replaces it
 * with the newest recorded state.
 */
const INSERT_LICENCE = `
  INSERT INTO licences (licence_key, tier, status, paddle_subscription_id,
                        plan, plan_unmatched,
                        max_postings_per_day, max_refreshes_per_day, max_saved_queries)
  SELECT ?1, ?2, 'active', ?3, ?4, 0, ?5, ?6, ?7 WHERE true
  ON CONFLICT DO NOTHING
  RETURNING licence_key`;

/**
 * A plan change, applied only if this event is still the newest recorded for
 * the subscription — otherwise a delayed older update would put a customer
 * back on the plan they had just left.
 */
const PLAN_CHANGE = `
  UPDATE licences
     SET plan = ?1, plan_unmatched = 0,
         max_postings_per_day = ?2,
         max_refreshes_per_day = ?3,
         max_saved_queries = ?4
   WHERE licence_key = ?5
     AND plan IS NOT ?1
     AND (?6 IS NULL
          OR (SELECT last_event_at FROM paddle_subscriptions WHERE subscription_id = ?7) = ?6)`;

function stateStatement(env, subscriptionId, event) {
  const { paddleStatus, access } = statusesFor(event);
  return env.DB.prepare(STATE_UPSERT)
    .bind(subscriptionId, occurredAt(event), paddleStatus, access);
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
  if (typeof type !== 'string') return { ok: true, action: 'ignored' };
  if (GRANTING.has(type)) return grant(env, ctx, event);
  if (STATUS_ONLY.has(type)) return moveStatus(env, event);
  if (UPDATING.has(type)) return update(env, event);
  if (ADJUSTING.has(type)) return adjust(env, event);
  return { ok: true, action: 'ignored', event_type: type };
}

/**
 * Whether an adjustment reverses the whole transaction. Paddle marks the
 * adjustment itself `full` or `partial`; where that is absent, every line
 * being `full` means the same.
 */
function isFullAdjustment(adjustment) {
  if (adjustment.type === 'full') return true;
  if (adjustment.type === 'partial') return false;
  const items = Array.isArray(adjustment.items) ? adjustment.items : [];
  return items.length > 0 && items.every((item) => item?.type === 'full');
}

/**
 * The licence key a refunded transaction paid for.
 *
 * The record written when the payment completed comes first, because it is
 * this Worker's own and cannot name the wrong subscription. The adjustment's
 * own subscription_id is the fallback, for a payment whose
 * transaction.completed was never delivered. A one-time purchase's licence is
 * keyed by its transaction id, which is the last resort.
 */
async function subscriptionForAdjustment(env, adjustment) {
  const txn = typeof adjustment.transaction_id === 'string' ? adjustment.transaction_id : null;
  if (txn) {
    const row = await env.DB.prepare(
      'SELECT subscription_id FROM paddle_transactions WHERE transaction_id = ?1'
    ).bind(txn).first();
    if (row?.subscription_id) return row.subscription_id;
  }
  return adjustment.subscription_id || txn;
}

/**
 * A refund or a chargeback.
 *
 * An APPROVED FULL refund, or any chargeback, ends access at once: the money
 * went back, so the licence it bought stops working. A partial refund changes
 * nothing, because it returns part of a payment for a period the customer
 * keeps. A refund still awaiting approval changes nothing yet — approval
 * arrives as adjustment.updated, and a rejected refund must not have cut the
 * customer off in the meantime. A chargeback is not awaited: by the time it is
 * reported the bank has already taken the money back.
 */
async function adjust(env, event) {
  const adjustment = event.data || {};
  const fullRefund = adjustment.action === 'refund'
    && adjustment.status === 'approved' && isFullAdjustment(adjustment);
  const chargeback = adjustment.action === 'chargeback';
  if (!fullRefund && !chargeback) return { ok: true, action: 'adjustment_no_change' };

  const kind = chargeback ? 'chargeback' : 'refund';
  const target = await subscriptionForAdjustment(env, adjustment);
  const changed = target
    ? (await env.DB.prepare(
        "UPDATE licences SET status = 'refunded' WHERE paddle_subscription_id = ?1"
      ).bind(target).run()).meta.changes
    : 0;
  if (!changed) {
    // Money has gone back and no licence was found to end. Worth a line: it is
    // either a purchase that never issued, or a licence still working unpaid.
    console.warn('paddle: refund matched no licence', { kind });
    return { ok: true, action: 'refund_no_licence', kind };
  }
  return { ok: true, action: 'refunded', kind };
}

async function grant(env, ctx, event) {
  const subscriptionId = subscriptionIdOf(event);
  if (!subscriptionId) {
    // A licence with no subscription id could never be cancelled, paused or
    // refunded by a later event.
    console.warn('paddle: granting event carried no id; nothing issued', { type: event.event_type });
    return { ok: true, action: 'no_subscription_id' };
  }

  const chosen = planForEvent(env, event);
  const caps = chosen.matched ? capsFor(chosen.plan) : null;
  const key = caps ? newLicenceKey() : null;

  // One transaction: record the event, create the licence if there is none,
  // then set its status from the newest recorded state. Separately, a
  // cancellation could land between the insert and the status and be lost.
  const statements = [stateStatement(env, subscriptionId, event)];
  if (event.event_type === 'transaction.completed' && typeof event.data?.id === 'string') {
    // Which subscription this payment was for. An adjustment names the
    // transaction it reverses, not always the subscription, so this record is
    // how a refund finds the licence it has to end.
    statements.push(env.DB.prepare(RECORD_TRANSACTION).bind(event.data.id, subscriptionId));
  }
  let insertAt = -1;
  if (caps) {
    // The caps the customer actually paid for. Before plans, every licence
    // fell back to the Worker's anti-abuse default and paying more bought
    // nothing at all.
    insertAt = statements.push(env.DB.prepare(INSERT_LICENCE).bind(
      key, tierFor(event), subscriptionId, caps.plan,
      caps.max_postings_per_day, caps.max_refreshes_per_day, caps.max_saved_queries)) - 1;
  }
  statements.push(env.DB.prepare(LICENCE_SYNC).bind(subscriptionId));
  statements.push(env.DB.prepare(LICENCE_BY_SUBSCRIPTION).bind(subscriptionId));
  const results = await env.DB.batch(statements);

  const fresh = results[0].results.length > 0;
  const created = insertAt >= 0 && results[insertAt].results.length > 0;
  const licence = results[results.length - 1].results[0] || null;

  if (created) {
    if (licence?.status !== 'active') {
      // A newer event had already ended this subscription. The row exists so
      // support can see it; a key emailed now would not work.
      return { ok: true, action: 'issued_inactive' };
    }
    await deliverLicence(env, ctx, event, key, caps, subscriptionId);
    return { ok: true, action: 'issued' };
  }
  if (licence) {
    // Reactivating an existing subscriber must not mint a second key. Not
    // gated on the price: the licence already exists, however it came to.
    return { ok: true, action: fresh ? 'reactivated' : 'stale_ignored' };
  }

  // An unrecognised price issues NOTHING. The fallback used to issue a
  // Standard licence and flag it, which handed a working key and a welcome
  // email to whoever bought something this Worker does not sell — another
  // product's price on the shared Paddle account, a test price, an id missing
  // from configuration. A licence wrongly withheld can be granted by hand with
  // a code minted in /admin; one wrongly issued is a live credential already
  // sitting in somebody's inbox.
  console.warn('paddle: price matched no plan; nothing issued', { prices: priceIdsIn(event) });
  return { ok: true, action: 'unmatched_product' };
}

/**
 * Send the licence to the buyer.
 *
 * The key is NOT returned in the webhook response: that goes to Paddle, not to
 * the customer. Delivery runs AFTER the licence is committed and OUTSIDE the
 * response, via waitUntil, because Paddle retries any non-2xx: a slow or
 * failing Resend call must not turn a successful issue into a redelivery. A
 * customer who does not receive the email can be sent it again; a customer
 * charged twice cannot be un-charged so easily.
 */
async function deliverLicence(env, ctx, event, key, caps, subscriptionId) {
  const work = (async () => {
    const who = await customerDetails(env, event);
    if (!who.email) {
      // Logged loudly and by cause. This is the failure that strands a paying
      // customer, and the only trace left will be this line.
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
  if (ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(work);
  else await work;
}

async function moveStatus(env, event) {
  const subscriptionId = subscriptionIdOf(event);
  if (!subscriptionId) return { ok: true, action: 'ignored', event_type: event.event_type };

  const results = await env.DB.batch([
    stateStatement(env, subscriptionId, event),
    env.DB.prepare(LICENCE_SYNC).bind(subscriptionId),
    env.DB.prepare(LICENCE_BY_SUBSCRIPTION).bind(subscriptionId),
  ]);
  const fresh = results[0].results.length > 0;
  const licence = results[2].results[0] || null;

  // Recorded even with no licence yet, so a grant delivered after it honours it.
  if (!licence) return { ok: true, action: 'status_no_licence' };
  if (!fresh) return { ok: true, action: 'stale_ignored' };
  return { ok: true, action: 'status_changed', status: licence.status };
}

async function update(env, event) {
  const subscriptionId = subscriptionIdOf(event);
  const row = subscriptionId
    ? await env.DB.prepare(LICENCE_BY_SUBSCRIPTION).bind(subscriptionId).first()
    : null;
  const chosen = planForEvent(env, event);
  const caps = row && chosen.matched && chosen.plan !== row.plan ? capsFor(chosen.plan) : null;
  // Read the previous plan BEFORE the update. Do not rely on the driver
  // handing back a detached row: if it returns a live reference, the UPDATE
  // rewrites it and the "from" reported here becomes the value it was changed
  // TO — an audit line that says a licence moved from global to global.
  const previous = row?.plan || 'unset';

  let results = null;
  if (subscriptionId) {
    // The status is recorded even when there is no licence or no plan change:
    // subscription.updated is also how a pause, a cancellation or a past-due
    // renewal is reported.
    const statements = [
      stateStatement(env, subscriptionId, event),
      env.DB.prepare(LICENCE_SYNC).bind(subscriptionId),
    ];
    if (caps) {
      statements.push(env.DB.prepare(PLAN_CHANGE).bind(
        caps.plan, caps.max_postings_per_day, caps.max_refreshes_per_day,
        caps.max_saved_queries, row.licence_key, occurredAt(event), subscriptionId));
    }
    results = await env.DB.batch(statements);
  }

  if (!row) {
    // An update for a subscription we never issued a licence for. Not an error
    // worth a retry — Paddle would redeliver forever — but it is worth saying,
    // because it means an earlier grant was missed.
    return { ok: true, action: 'update_no_licence' };
  }
  if (!chosen.matched) {
    // Do NOT fall back here. It would DOWNGRADE a paying customer to Standard
    // because a price id was not configured. Leave the caps alone and say so.
    console.warn('paddle: subscription.updated matched no plan; caps unchanged',
                 { had: row.plan || 'unset' });
    return { ok: true, action: 'update_unmatched_ignored' };
  }
  if (!caps) return { ok: true, action: 'update_no_plan_change' };
  if (results[2].meta.changes !== 1) return { ok: true, action: 'stale_ignored' };

  // Today's usage is deliberately NOT reset. An upgrade raises the ceiling,
  // which immediately gives back headroom on the same day; a downgrade lowers
  // it, and a customer already above the new cap simply has no headroom until
  // tomorrow. Zeroing the meter on either would make repeated plan changes a
  // way to fetch without limit.
  return { ok: true, action: 'plan_changed', from: previous, to: caps.plan };
}
