/**
 * A real SQLite D1 holding one licence, for the search-path tests.
 *
 * `state` reads the meter back from the table the Worker writes, summed over
 * every day, so a charge that lands on the wrong day still shows up.
 */
import { makeD1 } from './d1.mjs';

export const utcToday = () => new Date().toISOString().slice(0, 10);

export function makeSearchDB({
  key = 'L1', status = 'active', tier = 'managed', plan = null,
  maxPostings = 700, maxRefreshes = null, expiresAt = null,
  postings = 0, refreshes = 0,
} = {}) {
  const d1 = makeD1();
  d1.sqlite.prepare(
    `INSERT INTO licences (licence_key, tier, status, plan, max_postings_per_day,
                           max_refreshes_per_day, expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  ).run(key, tier, status, plan, maxPostings, maxRefreshes, expiresAt);
  if (postings || refreshes) {
    d1.sqlite.prepare(
      'INSERT INTO usage_daily (licence_key, day, refreshes, postings) VALUES (?, ?, ?, ?)'
    ).run(key, utcToday(), refreshes, postings);
  }
  Object.defineProperty(d1, 'state', {
    get() {
      return d1.query(
        `SELECT COALESCE(SUM(refreshes), 0) AS refreshes, COALESCE(SUM(postings), 0) AS postings
           FROM usage_daily WHERE licence_key = ?`, key)[0];
    },
  });
  return d1;
}
