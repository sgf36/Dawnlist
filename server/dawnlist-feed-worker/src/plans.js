/**
 * The plan catalogue — what a subscription buys, in one place.
 *
 * WHY THIS FILE EXISTS AT ALL
 * ---------------------------
 * Before it, `handlePaddleWebhook` issued a licence and set `tier`, but never
 * set `max_postings_per_day`. Every licence therefore fell back to the
 * Worker's 700/day anti-abuse default, and paying more bought nothing. The
 * schema had per-licence cap columns from the start and nothing ever wrote to
 * them — a table read but never written, which is exactly the blind spot the
 * wiring audit is for.
 *
 * THE CAP UNIT IS POSTINGS FETCHED. Never assessed. The billable event is the
 * fetch (1 credit = 1 job returned) and it happens before the free local
 * screen runs, so capping the downstream stage would govern about a tenth of
 * variable cost. `index.js` explains this at length; do not re-express a plan
 * in assessed postings because it reads better in marketing.
 *
 * WHY THE CAPS ARE HERE AND NOT IN THE APP
 * ----------------------------------------
 * A client-side cap on a desktop application is a suggestion. These values are
 * written to D1 at purchase and enforced server-side on every request.
 *
 * WHY PRICE IDS COME FROM env, NOT FROM THIS FILE
 * -----------------------------------------------
 * Paddle sandbox and production issue different price ids for the same
 * product. Hardcoding either one means the webhook silently assigns the
 * fallback plan in the other environment — a customer pays for Global and
 * receives Standard, with nothing in the logs saying so. So the ids are
 * configuration and the plans are code.
 */

/**
 * `key` is what goes in `licences.plan` and what the app displays.
 *
 * `maxPostingsPerDay` is the whole product difference between these two. The
 * refresh and saved-query numbers are anti-abuse limits, not the thing being
 * sold, and they move together.
 */
export const PLANS = {
  /**
   * Sized from measurement, not from a price. A comprehensive single-region
   * search measures ~513 postings fetched per day, so 700 clears it with
   * headroom and the cap does not quietly become the thing that decides
   * coverage. A user on this plan searching one country should never see a
   * cap message at all — if they do, the number is wrong, not the user.
   */
  standard: {
    key: 'standard',
    maxPostingsPerDay: 700,
    maxRefreshesPerDay: 3,
    maxSavedQueries: 10,
  },

  /**
   * For sweeps that span many countries.
   *
   * PROVISIONAL — the ceiling is an estimate and is the one number in this
   * file that has not been measured. A single-region comprehensive day is
   * ~513 fetched; a global sweep multiplies that by the number of regions
   * actually queried, and nobody has yet run one to see. 2,500 is roughly
   * five regions' worth and is deliberately generous, because a cap that
   * binds on the plan sold as "global" would be the same mistake as capping
   * coverage to defend a low price.
   *
   * MEASURE THIS BEFORE LAUNCH: run a real multi-region sweep, read
   * `usage_daily.postings`, and set the number from what comes back. The
   * value is a D1 column, so correcting it is an UPDATE and not a release.
   */
  global: {
    key: 'global',
    maxPostingsPerDay: 2500,
    maxRefreshesPerDay: 6,
    maxSavedQueries: 30,
  },

  /**
   * A trial demonstrates the mechanism, not the full product.
   *
   * A card-free trial at a full cap is worth real money in feed credits and
   * is farmable, so this is deliberately small enough that farming it is not
   * worth the effort. Anyone who hits it is seeing the product work, which is
   * what a trial is for.
   */
  trial: {
    key: 'trial',
    maxPostingsPerDay: 30,
    maxRefreshesPerDay: 2,
    maxSavedQueries: 3,
  },
};

/** What an unrecognised or absent plan falls back to. */
export const FALLBACK_PLAN = 'standard';

/**
 * Which plan a Paddle price id maps to.
 *
 * Configured as `PADDLE_PRICE_STANDARD` and `PADDLE_PRICE_GLOBAL`. Each may
 * hold several comma-separated ids, because a single plan legitimately has
 * more than one price: monthly and annual, and one per currency where Paddle
 * issues separate ids.
 *
 * Returns null when nothing matches, so the caller can decide — and log —
 * rather than having a default silently chosen here.
 */
export function planForPriceId(env, priceId) {
  if (!priceId) return null;
  const table = {
    standard: env.PADDLE_PRICE_STANDARD,
    global: env.PADDLE_PRICE_GLOBAL,
  };
  for (const [plan, configured] of Object.entries(table)) {
    if (!configured) continue;
    const ids = String(configured).split(',').map((s) => s.trim()).filter(Boolean);
    if (ids.includes(priceId)) return plan;
  }
  return null;
}

/**
 * The plan a Paddle event bought, and how confident we are.
 *
 * Returns `{ plan, matched }`. `matched: false` means no price id in the
 * event matched anything configured, and the caller is getting the fallback.
 * That distinction is the point: silently defaulting is how a Global customer
 * ends up on Standard caps with nothing anywhere recording that it happened.
 */
export function planForEvent(env, event) {
  const items = event?.data?.items || [];
  for (const item of items) {
    const priceId = item?.price?.id || item?.price_id;
    const plan = planForPriceId(env, priceId);
    if (plan) return { plan, matched: true, priceId };
  }
  return { plan: FALLBACK_PLAN, matched: false, priceId: null };
}

/** The caps to write to D1 for a plan. Unknown plans get the fallback. */
export function capsFor(planKey) {
  const plan = PLANS[planKey] || PLANS[FALLBACK_PLAN];
  return {
    plan: plan.key,
    max_postings_per_day: plan.maxPostingsPerDay,
    max_refreshes_per_day: plan.maxRefreshesPerDay,
    max_saved_queries: plan.maxSavedQueries,
  };
}
