/**
 * Plans: what a purchase actually buys, and what an upgrade actually changes.
 *
 * The bug this file exists to prevent is the one that was there: the webhook
 * set `tier` and never set the cap columns, so every licence fell back to the
 * Worker default and paying more bought nothing at all. Every test below
 * asserts on the CAP COLUMNS, not on the plan name, because the name is
 * decoration and the caps are what the request path enforces.
 *
 * Run: node test/plans.test.mjs
 */
import assert from 'node:assert';
import { handlePaddleWebhook } from '../src/paddle.js';
import { PLANS, capsFor, planForEvent, planForPriceId, sellablePlans } from '../src/plans.js';

const SECRET = 'pdl_ntfset_01test_secretvalue_for_local_checks_only';
let passed = 0, failed = 0;

async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

async function sign(body, secret = SECRET, ts = Math.floor(Date.now() / 1000)) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const mac = await crypto.subtle.sign('HMAC', key,
    new TextEncoder().encode(`${ts}:${body}`));
  const h1 = [...new Uint8Array(mac)].map(b => b.toString(16).padStart(2, '0')).join('');
  return `ts=${ts};h1=${h1}`;
}

/** D1 stub that records the cap columns, which is the whole point here. */
function makeDB(seed = []) {
  const licences = [...seed], events = [];
  return {
    licences, events,
    prepare(sql) {
      return {
        _args: [],
        bind(...a) { this._args = a; return this; },
        async first() {
          if (sql.includes('FROM webhook_events')) {
            return events.find(e => e.event_id === this._args[0]) || null;
          }
          if (sql.includes('FROM licences')) {
            return licences.find(l => l.paddle_subscription_id === this._args[0]) || null;
          }
          return null;
        },
        async run() {
          if (sql.includes('INSERT INTO licences')) {
            licences.push({
              licence_key: this._args[0], tier: this._args[1],
              status: 'active', paddle_subscription_id: this._args[2],
              plan: this._args[3], plan_unmatched: this._args[4],
              max_postings_per_day: this._args[5],
              max_refreshes_per_day: this._args[6],
              max_saved_queries: this._args[7],
            });
          } else if (sql.includes('UPDATE licences') && sql.includes('SET plan')) {
            const row = licences.find(l => l.licence_key === this._args[4]);
            if (row) {
              row.plan = this._args[0];
              row.plan_unmatched = 0;
              row.max_postings_per_day = this._args[1];
              row.max_refreshes_per_day = this._args[2];
              row.max_saved_queries = this._args[3];
            }
          } else if (sql.includes('INSERT INTO webhook_events')) {
            events.push({ event_id: this._args[0] });
          }
          return { success: true };
        },
      };
    },
  };
}

const ENV = {
  PADDLE_WEBHOOK_SECRET: SECRET,
  PADDLE_PRICE_STANDARD: 'pri_standard_monthly,pri_standard_annual',
  PADDLE_PRICE_GLOBAL: 'pri_global_monthly',
};

async function post(env, db, event) {
  const body = JSON.stringify(event);
  const req = new Request('https://w/paddle/webhook', {
    method: 'POST', body,
    headers: { 'Paddle-Signature': await sign(body) },
  });
  const res = await handlePaddleWebhook(req, { ...env, DB: db });
  return res.json();
}

function subEvent(type, priceId, id = 'sub_1', eventId = `evt_${Math.random()}`) {
  return {
    event_id: eventId, event_type: type,
    data: { id, items: priceId ? [{ price: { id: priceId, billing_cycle: { interval: 'month' } } }] : [] },
  };
}

// ---------------------------------------------------------------------------
// The ladder the app renders as "upgrade to ...". A plan nobody can buy must
// never appear in it.
// ---------------------------------------------------------------------------

await test('the upgrade ladder offers only plans a customer can buy', () => {
  const keys = sellablePlans().map((p) => p.key);
  assert.deepEqual(keys, ['standard', 'global'],
    'the ladder is the two sold plans, smallest ceiling first');
});

await test('trial is never offered as an upgrade', () => {
  assert.ok(!sellablePlans().some((p) => p.key === 'trial'),
    'a trial is granted by code, not bought; offering it is a support ticket');
});

await test('the owner plan exists, is capped, and is not for sale', () => {
  // Spencer must be able to run the shipped app on his own machines without
  // buying it. The cap is not a restriction on him — it bounds a runaway loop
  // spending real feed credits against his own subscription.
  assert.ok(PLANS.owner, 'the developer needs a licence that is not a purchase');
  assert.ok(!sellablePlans().some((p) => p.key === 'owner'),
    'advertising the developer allowance would offer a plan nobody can buy');
  assert.ok(PLANS.owner.maxPostingsPerDay > PLANS.standard.maxPostingsPerDay,
    'dogfooding must not be tighter than the plan being sold');
  assert.ok(Number.isInteger(PLANS.owner.maxPostingsPerDay)
    && PLANS.owner.maxPostingsPerDay > 0,
    'uncapped would make the owner licence the only unbounded spender');
});

await test('capsFor writes the owner caps, not the standard fallback', () => {
  const caps = capsFor('owner');
  assert.equal(caps.plan, 'owner');
  assert.equal(caps.max_postings_per_day, PLANS.owner.maxPostingsPerDay);
});

console.log('price id mapping');

await test('a configured price id maps to its plan', () => {
  assert.equal(planForPriceId(ENV, 'pri_global_monthly'), 'global');
  assert.equal(planForPriceId(ENV, 'pri_standard_monthly'), 'standard');
});

await test('one plan can have several price ids (monthly, annual, currencies)', () => {
  assert.equal(planForPriceId(ENV, 'pri_standard_annual'), 'standard');
});

await test('an unknown price id matches nothing, rather than defaulting here', () => {
  assert.equal(planForPriceId(ENV, 'pri_who_knows'), null);
});

await test('planForEvent reports whether it actually matched', () => {
  const matched = planForEvent(ENV, subEvent('subscription.created', 'pri_global_monthly'));
  assert.equal(matched.plan, 'global');
  assert.equal(matched.matched, true);

  const missed = planForEvent(ENV, subEvent('subscription.created', 'pri_nope'));
  assert.equal(missed.plan, 'standard');   // the fallback
  assert.equal(missed.matched, false);     // and it says so
});

console.log('issuing');

await test('a purchase writes the caps it paid for', async () => {
  const db = makeDB();
  await post(ENV, db, subEvent('subscription.created', 'pri_global_monthly'));
  const row = db.licences[0];
  assert.equal(row.plan, 'global');
  assert.equal(row.max_postings_per_day, PLANS.global.maxPostingsPerDay);
  assert.equal(row.max_refreshes_per_day, PLANS.global.maxRefreshesPerDay);
});

await test('the standard plan writes the standard caps, not the default', async () => {
  const db = makeDB();
  await post(ENV, db, subEvent('subscription.created', 'pri_standard_monthly'));
  assert.equal(db.licences[0].max_postings_per_day, PLANS.standard.maxPostingsPerDay);
});

await test('an unmatched price takes the fallback AND is flagged', async () => {
  // Flagged, because a customer on the wrong caps is otherwise unfindable:
  // the webhook logs counts, never payloads, so the event itself is gone.
  const db = makeDB();
  await post(ENV, db, subEvent('subscription.created', 'pri_unconfigured'));
  assert.equal(db.licences[0].plan, 'standard');
  assert.equal(db.licences[0].plan_unmatched, 1);
});

console.log('upgrading and downgrading');

await test('an upgrade raises the caps', async () => {
  const db = makeDB();
  await post(ENV, db, subEvent('subscription.created', 'pri_standard_monthly'));
  assert.equal(db.licences[0].max_postings_per_day, 700);

  const out = await post(ENV, db,
    subEvent('subscription.updated', 'pri_global_monthly'));
  assert.equal(out.action, 'plan_changed');
  assert.equal(out.from, 'standard');
  assert.equal(out.to, 'global');
  assert.equal(db.licences[0].max_postings_per_day, PLANS.global.maxPostingsPerDay);
});

await test('a downgrade lowers them again', async () => {
  const db = makeDB();
  await post(ENV, db, subEvent('subscription.created', 'pri_global_monthly'));
  await post(ENV, db, subEvent('subscription.updated', 'pri_standard_monthly'));
  assert.equal(db.licences[0].plan, 'standard');
  assert.equal(db.licences[0].max_postings_per_day, PLANS.standard.maxPostingsPerDay);
});

await test('an update that is NOT a plan change writes nothing', async () => {
  // subscription.updated fires for a new card or a billing address too.
  // Rewriting caps on those would silently reset a licence support had raised.
  const db = makeDB();
  await post(ENV, db, subEvent('subscription.created', 'pri_global_monthly'));
  db.licences[0].max_postings_per_day = 9999;      // a manual support raise
  const out = await post(ENV, db, subEvent('subscription.updated', 'pri_global_monthly'));
  assert.equal(out.action, 'update_no_plan_change');
  assert.equal(db.licences[0].max_postings_per_day, 9999, 'the manual raise survived');
});

await test('an update with an unmatched price does NOT downgrade a paying customer', async () => {
  // The fallback is right when issuing (a licence must exist) and wrong here,
  // where it would move a Global customer onto Standard caps because someone
  // forgot to configure a price id.
  const db = makeDB();
  await post(ENV, db, subEvent('subscription.created', 'pri_global_monthly'));
  const out = await post(ENV, db, subEvent('subscription.updated', 'pri_not_configured'));
  assert.equal(out.action, 'update_unmatched_ignored');
  assert.equal(db.licences[0].plan, 'global');
  assert.equal(db.licences[0].max_postings_per_day, PLANS.global.maxPostingsPerDay);
});

await test('an update for a subscription with no licence is acknowledged, not retried', async () => {
  // Returning non-2xx would make Paddle redeliver this forever.
  const db = makeDB();
  const out = await post(ENV, db, subEvent('subscription.updated', 'pri_global_monthly', 'sub_unknown'));
  assert.equal(out.action, 'update_no_licence');
  assert.equal(out.ok, true);
});

console.log('cap values');

await test('every plan has a strictly larger ceiling than the one below', () => {
  const ladder = [PLANS.trial, PLANS.standard, PLANS.global];
  for (let i = 1; i < ladder.length; i++) {
    assert.ok(ladder[i].maxPostingsPerDay > ladder[i - 1].maxPostingsPerDay,
      `${ladder[i].key} must allow more than ${ladder[i - 1].key}`);
  }
});

await test('standard clears a measured comprehensive single-region day', () => {
  // ~513 postings fetched per day, measured. If standard ever drops below
  // that, the cap has quietly become the thing deciding coverage.
  assert.ok(PLANS.standard.maxPostingsPerDay > 513,
    'standard must sit above the measured 513/day, not on it');
});

await test('capsFor falls back rather than returning undefined columns', () => {
  const caps = capsFor('no_such_plan');
  assert.equal(caps.plan, 'standard');
  assert.ok(Number.isInteger(caps.max_postings_per_day));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
