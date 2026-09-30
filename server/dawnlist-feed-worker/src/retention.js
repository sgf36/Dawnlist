/**
 * Deleting what this Worker no longer needs, run by the cron trigger in
 * wrangler.jsonc.
 *
 * WHAT GOES, AND WHY THEN
 *   code_attempts   after 2 days. It exists only to rate-limit today's failed
 *                   redemptions, and each row is tied to a connection. Kept any
 *                   longer it is a record of who tried codes, which nothing
 *                   here needs.
 *   linkedin_cursors after 30 days. Each holds a hash of a search and the
 *                   keyword searched, which is a record of what somebody looked
 *                   for and is only useful while they keep running that search.
 *   linkedin_usage  after 35 days. Page counts for the daily quota; nothing
 *   (_global)       reads them after the day has rolled.
 *   webhook_events  after 90 days. It exists so a Paddle retry is recognised
 *                   as one; Paddle stops retrying long before that.
 *
 * WHAT DOES NOT GO: usage_daily. The privacy policy (store/privacy-policy.md)
 * says usage counts are kept for twelve months after a licence ends, and they
 * are also the measurement the dataset-crossover decision rests on. Deleting
 * them at a fixed age would break that promise for every licence still
 * running, so they are not part of this purge.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

export const CODE_ATTEMPTS_DAYS = 2;
export const WEBHOOK_EVENTS_DAYS = 90;
export const LINKEDIN_CURSOR_DAYS = 30;
export const LINKEDIN_USAGE_DAYS = 35;

/**
 * webhook_events.received_at is written by datetime('now') as
 * 'YYYY-MM-DD HH:MM:SS'. The cutoff has to be the same shape, because the
 * comparison is on text and an ISO 'T' sorts after the space.
 */
function sqliteDateTime(date) {
  return date.toISOString().slice(0, 19).replace('T', ' ');
}

export async function purgeExpired(env, now = new Date()) {
  const attemptsBefore = new Date(now.getTime() - CODE_ATTEMPTS_DAYS * DAY_MS)
    .toISOString().slice(0, 10);
  const eventsBefore = sqliteDateTime(new Date(now.getTime() - WEBHOOK_EVENTS_DAYS * DAY_MS));

  const cursorsBefore = sqliteDateTime(new Date(now.getTime() - LINKEDIN_CURSOR_DAYS * DAY_MS));
  const usageBefore = new Date(now.getTime() - LINKEDIN_USAGE_DAYS * DAY_MS)
    .toISOString().slice(0, 10);

  const [attempts, events, cursors, usage, usageGlobal] = await env.DB.batch([
    env.DB.prepare('DELETE FROM code_attempts WHERE day < ?1').bind(attemptsBefore),
    env.DB.prepare('DELETE FROM webhook_events WHERE received_at < ?1').bind(eventsBefore),
    env.DB.prepare('DELETE FROM linkedin_cursors WHERE updated_at < ?1').bind(cursorsBefore),
    env.DB.prepare('DELETE FROM linkedin_usage WHERE day < ?1').bind(usageBefore),
    env.DB.prepare('DELETE FROM linkedin_usage_global WHERE day < ?1').bind(usageBefore),
  ]);
  const purged = {
    code_attempts: attempts.meta?.changes ?? 0,
    webhook_events: events.meta?.changes ?? 0,
    linkedin_cursors: cursors.meta?.changes ?? 0,
    linkedin_usage: (usage.meta?.changes ?? 0) + (usageGlobal.meta?.changes ?? 0),
  };
  console.log('retention: purged', purged);
  return purged;
}
