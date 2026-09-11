/**
 * What is deleted on the schedule, what is kept, and what is never stored.
 * Run: node test/retention.test.mjs
 *
 * What these protect, in one line each:
 *   - failed-attempt rows go after two days, webhook events after ninety;
 *   - usage counts are NOT purged by age, because the privacy policy keeps them
 *     for twelve months after a licence ends;
 *   - the purge is wired to a cron trigger, not merely written;
 *   - a failed redemption stores a hash of the address, never the address.
 */
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import worker from '../src/index.js';
import { clientHash, handleRedeem } from '../src/codes.js';
import { newLicenceKey } from '../src/paddle.js';
import { purgeExpired } from '../src/retention.js';
import { makeD1 } from './d1.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const NOW = new Date('2026-09-11T12:00:00Z');

/** Rows either side of each cutoff, measured from NOW. */
function seedAges(db) {
  db.sqlite.exec("INSERT INTO licences (licence_key, tier) VALUES ('L1', 'managed')");
  const attempt = db.sqlite.prepare(
    'INSERT INTO code_attempts (client_hash, day, failures) VALUES (?, ?, 1)');
  for (const day of ['2026-09-11', '2026-09-10', '2026-09-09', '2026-09-08', '2026-08-01']) {
    attempt.run(`hash-${day}`, day);
  }
  const event = db.sqlite.prepare(
    "INSERT INTO webhook_events (event_id, event_type, action, received_at) VALUES (?, 't', 'a', ?)");
  for (const [id, at] of [['recent', '2026-09-10 08:00:00'], ['day-89', '2026-06-14 12:00:01'],
                          ['day-91', '2026-06-12 11:59:59'], ['ancient', '2025-01-01 00:00:00']]) {
    event.run(id, at);
  }
  const usage = db.sqlite.prepare(
    'INSERT INTO usage_daily (licence_key, day, refreshes, postings) VALUES (?, ?, 1, 10)');
  for (const day of ['2026-09-11', '2026-06-01', '2025-08-01']) usage.run('L1', day);
}

console.log('the purge');

await test('failed-attempt rows older than two days are deleted, and the last two days kept', async () => {
  const db = makeD1();
  seedAges(db);
  await purgeExpired({ DB: db }, NOW);
  assert.deepEqual(db.query('SELECT day FROM code_attempts ORDER BY day').map((r) => r.day),
    ['2026-09-09', '2026-09-10', '2026-09-11']);
});

await test('webhook events older than ninety days are deleted, and newer ones kept', async () => {
  const db = makeD1();
  seedAges(db);
  await purgeExpired({ DB: db }, NOW);
  assert.deepEqual(db.query('SELECT event_id FROM webhook_events ORDER BY event_id').map((r) => r.event_id),
    ['day-89', 'recent']);
});

await test('usage counts are kept whatever their age', async () => {
  const db = makeD1();
  seedAges(db);
  await purgeExpired({ DB: db }, NOW);
  assert.equal(db.query('SELECT COUNT(*) AS n FROM usage_daily')[0].n, 3,
    'a year-old count may belong to a licence that ended last month');
});

await test('the purge logs how many rows went, and nothing that identifies them', async () => {
  const db = makeD1();
  seedAges(db);
  const lines = [];
  const real = console.log;
  console.log = (...a) => lines.push(JSON.stringify(a));
  let counts;
  try { counts = await purgeExpired({ DB: db }, NOW); } finally { console.log = real; }
  assert.deepEqual(counts, { code_attempts: 2, webhook_events: 2 });
  assert.equal(lines.length, 1);
  assert.ok(!/hash-|ancient|day-91/.test(lines[0]), lines[0]);
});

console.log('\nwired to a schedule');

await test('the scheduled handler runs the purge', async () => {
  const db = makeD1();
  db.sqlite.exec(`INSERT INTO code_attempts (client_hash, day, failures) VALUES ('old', '2000-01-01', 1);
                  INSERT INTO code_attempts (client_hash, day, failures)
                    VALUES ('today', '${new Date().toISOString().slice(0, 10)}', 1);`);
  const pending = [];
  await worker.scheduled({ cron: '17 3 * * *' }, { DB: db }, { waitUntil: (p) => pending.push(p) });
  await Promise.all(pending);
  assert.deepEqual(db.query('SELECT client_hash FROM code_attempts').map((r) => r.client_hash),
    ['today']);
});

await test('wrangler.jsonc declares the cron trigger that calls it', async () => {
  const text = readFileSync(new URL('../wrangler.jsonc', import.meta.url), 'utf8');
  const config = JSON.parse(text.replace(/^\s*\/\/.*$/gm, ''));
  assert.ok(Array.isArray(config.triggers?.crons) && config.triggers.crons.length > 0,
    'a purge nothing schedules is a retention period nobody enforces');
});

console.log('\nwhat a failed redemption stores');

const attemptFrom = (address) => new Request('https://w/redeem', {
  method: 'POST', body: JSON.stringify({ code: 'DL-ZZZZ-ZZZZ-ZZZZ-ZZZZ' }),
  headers: { 'cf-connecting-ip': address, 'content-type': 'application/json' },
});

await test('a failed redemption stores no address', async () => {
  const db = makeD1();
  await handleRedeem(attemptFrom('203.0.113.7'), { DB: db }, newLicenceKey);
  const rows = db.query('SELECT * FROM code_attempts');
  assert.equal(rows.length, 1, 'positive control: the failure was recorded');
  assert.ok(!JSON.stringify(rows).includes('203.0.113.7'));
  assert.match(rows[0].client_hash, /^[0-9a-f]{64}$/);
});

await test('with CLIENT_HASH_SECRET set, what is stored is keyed by it', async () => {
  const db = makeD1();
  await handleRedeem(attemptFrom('203.0.113.7'), { DB: db, CLIENT_HASH_SECRET: 'secret-one' },
    newLicenceKey);
  const stored = db.query('SELECT client_hash FROM code_attempts')[0].client_hash;
  const req = attemptFrom('203.0.113.7');
  assert.equal(stored, await clientHash({ CLIENT_HASH_SECRET: 'secret-one' }, req));
  assert.notEqual(stored, await clientHash({ CLIENT_HASH_SECRET: 'secret-two' }, req),
    'a different secret gives a different value, so the secret is really used');
  assert.notEqual(stored, await clientHash({}, req), 'and it is not the unkeyed fallback');
});

await test('the rate limit still counts each connection separately', async () => {
  const db = makeD1();
  for (let i = 0; i < 20; i++) await handleRedeem(attemptFrom('198.51.100.1'), { DB: db }, newLicenceKey);
  const limited = await handleRedeem(attemptFrom('198.51.100.1'), { DB: db }, newLicenceKey);
  const other = await handleRedeem(attemptFrom('198.51.100.2'), { DB: db }, newLicenceKey);
  assert.equal(limited.status, 429);
  assert.equal(other.status, 403, 'another connection is refused the code, not rate limited');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
