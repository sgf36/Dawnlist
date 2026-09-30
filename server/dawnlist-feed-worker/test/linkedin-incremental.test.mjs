/**
 * The incremental LinkedIn scan: newest-first, stop at a cutoff, spend few pages.
 * Run: node test/linkedin-incremental.test.mjs
 *
 * LinkedIn's daily quota is the limit on this provider, so the number of pages a
 * scan spends IS the feature. A simulated feed lets each case assert that number.
 *
 * What these protect, in one line each:
 *   - a first scan reads a few days back, not the whole window;
 *   - a later scan starts a day before the cursor and reads only what is new;
 *   - a batch is sized from the observed density, so a scarce quota is not spent
 *     on pages past the cutoff;
 *   - a scan that did not reach its cutoff says so, and reports the newest
 *     posting it READ (never the clock), so the cursor cannot skip anything;
 *   - a response that is not newest-first is not trusted to stop a scan.
 */
import assert from 'node:assert';
import {
  makeLinkedInAdapter, FIRST_RUN_DAYS, OVERLAP_MS, PAGE_SIZE, PARALLEL,
} from '../src/linkedin.js';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.stack.split('\n').slice(0, 3).join('\n       ')}`); failed++; }
}

class HttpError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}
const adapter = makeLinkedInAdapter({ HttpError });
const env = { LINKEDIN_ACCESS_TOKEN: 'tok' };
const HOUR = 3600 * 1000, DAY = 24 * HOUR;
const NOW = new Date('2026-09-30T12:00:00Z');
// the API lags: its newest posting is about two days old
const NEWEST = NOW.getTime() - 2 * DAY;

/**
 * A feed of `total` postings, newest first, one every `stepMs`. `titleFor(i)`
 * names row i. Records which pages were asked for.
 */
function feed({ stepMs = HOUR, total = 5000, newest = NEWEST, titleFor = (i) => `Hotel Manager ${i}`, shuffle = null } = {}) {
  const asked = [];
  const rows = Array.from({ length: total }, (_, i) => ({
    jobPostingUrl: `https://www.linkedin.com/ad-library/job/detail/${4400000000 + i}`,
    jobDetails: {
      jobTitle: titleFor(i), jobLocation: 'London, England, United Kingdom', organizationName: 'Acme',
      jobDescription: 'x', jobListTimeInMilliseconds: newest - i * stepMs,
    },
  }));
  const fn = async (url) => {
    const u = new URL(url);
    const start = Number(u.searchParams.get('start'));
    asked.push(start / PAGE_SIZE);
    let slice = rows.slice(start, start + PAGE_SIZE);
    if (shuffle) slice = shuffle(start, slice);
    return { ok: true, status: 200, json: async () => ({
      paging: { total, links: start + PAGE_SIZE < total ? [{ rel: 'next' }] : [] }, elements: slice,
    }), text: async () => '' };
  };
  fn.asked = asked;
  fn.rows = rows;
  return fn;
}

const go = (q, f, deps = {}) => adapter.search(env, { maxResults: 500, titles: ['Hotel Manager'], ...q }, 500,
  { fetch: f, today: NOW, ...deps });

console.log('how far back');

await test('a first scan reads back FIRST_RUN_DAYS, not the whole window', async () => {
  const f = feed();                                  // 24 postings a day = 1 page a day
  const r = await go({ postedWithinDays: 45 }, f, { cursors: {}, pages: 40 });
  const k = r.keywords[0];
  assert.equal(k.complete, true);
  assert.ok(f.asked.length <= FIRST_RUN_DAYS + 2, `${f.asked.length} pages for ${FIRST_RUN_DAYS} days`);
  assert.ok(f.asked.length >= FIRST_RUN_DAYS, 'and it really did read that far');
});

await test('a later scan starts a day BEFORE the cursor and reads only what is new', async () => {
  const cursor = NEWEST - 1 * DAY;                   // last scan saw everything up to a day ago
  const f = feed();
  const r = await go({ postedWithinDays: 45 }, f, { cursors: { 'Hotel Manager': cursor }, pages: 40 });
  assert.equal(r.keywords[0].complete, true);
  // new since cursor = 1 day = 1 page, overlap = 1 day = 1 page, plus the page that crosses the cutoff
  assert.ok(f.asked.length <= 4, `${f.asked.length} pages: ${f.asked}`);
});

await test('the search window still bounds an old cursor', async () => {
  const f = feed({ total: 5000 });
  const r = await go({ postedWithinDays: 5 }, f, { cursors: { 'Hotel Manager': NEWEST - 30 * DAY }, pages: 100 });
  assert.equal(r.keywords[0].complete, true);
  assert.ok(f.asked.length <= 5 + 2, `${f.asked.length} pages for a 5-day window`);
});

await test('with no cursors object the scan is not incremental: it goes back to the window', async () => {
  const f = feed();
  const r = await go({ postedWithinDays: 10 }, f, { pages: 40 });
  assert.equal(r.keywords[0].complete, true);
  assert.ok(f.asked.length >= 9, `${f.asked.length} pages should cover a 10-day window at 1 page a day`);
});

console.log('spending few pages');

await test('the batch is sized from the observed density, so it does not overshoot the cutoff', async () => {
  const f = feed({ stepMs: HOUR });                  // needs ~3 more pages after the first
  await go({}, f, { cursors: {}, pages: 40 });
  const calls = f.asked.length;
  assert.ok(calls <= FIRST_RUN_DAYS + 2, `${calls} pages; a fixed batch of ${PARALLEL} would have spent ${PARALLEL + 1}`);
});

await test('a dense feed spends the allowance and no more, and says it did not finish', async () => {
  const f = feed({ stepMs: 60 * 1000 });             // a posting a minute: 60 pages a day
  const r = await go({}, f, { cursors: {}, pages: 4 });
  assert.equal(f.asked.length, 4);
  assert.equal(r.pages, 4);
  assert.equal(r.keywords[0].complete, false);
  assert.equal(r.exhausted, false);
});

await test('zero pages allowed makes no request and reports an unfinished scan', async () => {
  const f = feed();
  const r = await go({}, f, { cursors: {}, pages: 0 });
  assert.equal(f.asked.length, 0);
  assert.equal(r.keywords[0].complete, false);
});

await test('the row limit ends a scan early and is reported as unfinished', async () => {
  const f = feed({ stepMs: 60 * 1000 });
  const r = await go({ maxResults: 10 }, f, { cursors: {}, pages: 40 });
  assert.equal(r.jobs.length, 10);
  assert.equal(r.keywords[0].complete, false, 'stopping for the row limit has not reached the cutoff');
});

console.log('the cursor');

await test('newestMs is the newest posting READ, not the clock, and counts rows the filters dropped', async () => {
  // the newest posting is a Cashier, dropped by the title filter
  const f = feed({ titleFor: (i) => (i === 0 ? 'Cashier' : `Hotel Manager ${i}`) });
  const r = await go({}, f, { cursors: {}, pages: 40 });
  assert.equal(r.keywords[0].newestMs, NEWEST);
  assert.ok(r.keywords[0].newestMs < NOW.getTime() - DAY, 'not wall-clock time: the API lags');
  assert.ok(!r.jobs.some((j) => j.title === 'Cashier'));
});

await test('an incomplete scan reports newestMs too, for the record, but complete:false', async () => {
  const f = feed({ stepMs: 60 * 1000 });
  const r = await go({}, f, { cursors: {}, pages: 2 });
  assert.equal(r.keywords[0].complete, false);
  assert.equal(r.keywords[0].newestMs, NEWEST);
});

await test('no rows at all is complete with no cursor to move', async () => {
  const f = feed({ total: 0 });
  const r = await go({}, f, { cursors: {}, pages: 10 });
  assert.equal(r.keywords[0].complete, true);
  assert.equal(r.keywords[0].newestMs, 0);
});

await test('rows older than the cutoff are not returned', async () => {
  const f = feed();
  const r = await go({}, f, { cursors: {}, pages: 40 });
  // measured from the newest posting READ, not from the clock: the API lags
  const cutoff = NEWEST - FIRST_RUN_DAYS * DAY;
  assert.ok(r.jobs.length > 0);
  assert.ok(r.jobs.every((j) => new Date(j.posted_at).getTime() >= cutoff - DAY), 'posted_at is a date');
  assert.ok(r.jobs.length < f.rows.length / 10, 'and it did not return the whole feed');
});

await test('each keyword keeps its own cursor and its own completeness', async () => {
  const narrow = feed({ total: 30, stepMs: 4 * DAY });        // ends after a page or two
  const broad = feed({ stepMs: 60 * 1000 });
  const f = async (url) => (new URL(url).searchParams.get('keyword') === 'Narrow Title' ? narrow(url) : broad(url));
  const r = await adapter.search(env, { titles: ['Narrow Title', 'Hotel Manager'], maxResults: 500, searchType: 'both' }, 500,
    { fetch: f, today: NOW, cursors: {}, pages: 10 });
  const byKw = Object.fromEntries(r.keywords.map((k) => [k.keyword, k]));
  assert.equal(byKw['Narrow Title'].complete, true);
  assert.equal(byKw['Hotel Manager'].complete, false);
});

await test('pages a narrow keyword did not need roll over to the next one', async () => {
  const narrow = feed({ total: 10, stepMs: DAY });
  const broad = feed({ stepMs: 60 * 1000 });
  const f = async (url) => (new URL(url).searchParams.get('keyword') === 'Narrow Title' ? narrow(url) : broad(url));
  const r = await adapter.search(env, { titles: ['Narrow Title', 'Hotel Manager'], maxResults: 500, searchType: 'both' }, 500,
    { fetch: f, today: NOW, cursors: {}, pages: 10 });
  const byKw = Object.fromEntries(r.keywords.map((k) => [k.keyword, k]));
  assert.equal(byKw['Narrow Title'].pages, 1);
  assert.equal(byKw['Hotel Manager'].pages, 9, 'the other nine pages went to the keyword that needed them');
  assert.equal(r.pages, 10);
});

console.log('not trusting the order');

await test('a page that starts newer than the last one ended disables the cutoff for that keyword', async () => {
  // page 1 is swapped with page 0's rows, so it starts NEWER than page 0 ended
  const f = feed({ total: 500, stepMs: HOUR,
    shuffle: (start, slice) => (start === PAGE_SIZE ? Array.from({ length: PAGE_SIZE }, (_, i) => ({
      ...slice[i], jobDetails: { ...slice[i].jobDetails, jobListTimeInMilliseconds: NEWEST + (PAGE_SIZE - i) * HOUR } })) : slice) });
  const r = await go({}, f, { cursors: {}, pages: 6 });
  assert.equal(r.keywords[0].unsorted, true);
  assert.equal(r.keywords[0].complete, false, 'an unsorted feed cannot prove it reached the cutoff');
  assert.equal(f.asked.length, 6, 'so the page budget bounded it instead');
});

console.log('failures report the pages they spent');

await test('an error carries the pages already spent so the caller can refund the rest', async () => {
  let calls = 0;
  const f = async () => { calls++; return { ok: false, status: 429, json: async () => ({}), text: async () => '{}' }; };
  await assert.rejects(() => go({}, f, { cursors: {}, pages: 10 }), (e) => {
    assert.equal(e.code, 'rate_limited');
    assert.equal(e.pagesUsed, calls);
    assert.ok(e.pagesUsed >= 1 && e.pagesUsed < 10);
    return true;
  });
});

await test('the overlap is a day, long enough for the API lag seen in the data', () => {
  assert.equal(OVERLAP_MS, DAY);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
