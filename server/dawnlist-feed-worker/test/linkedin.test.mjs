/**
 * The Worker's LinkedIn adapter, network injected.
 * Run: node test/linkedin.test.mjs
 *
 * What these protect, in one line each:
 *   - the request is the shape the live API accepts (it 400'd on every search
 *     for want of it, and no test could see that: the transport was mocked);
 *   - several titles are several requests, because LinkedIn ANDs the words;
 *   - what comes back is filtered by title, city, age and exclusions, because
 *     the API has no such filters and matches keywords against descriptions;
 *   - a short page is not the end, the scan is bounded, pages run in parallel;
 *   - an expired token is loud, and a mid-scan failure keeps what was read.
 */
import assert from 'node:assert';
import {
  makeLinkedInAdapter, keywordsFor, linkedinParams, encodeQuery, titleMatches,
  PAGE_TIMEOUT_MS, PAGE_BUDGET, MAX_PAGE_BUDGET, CACHE_TTL_SECONDS, PAGE_SIZE, PARALLEL, MAX_KEYWORDS, PROVIDER,
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
const TODAY = new Date('2026-09-30T12:00:00Z');

let counter = 0;
const el = (title, { loc = 'London, England, United Kingdom', desc = 'x', org = 'Acme', days = 1 } = {}) => ({
  jobPostingUrl: `https://www.linkedin.com/ad-library/job/detail/${4400000000 + (++counter)}`,
  jobDetails: {
    jobTitle: title, jobLocation: loc, organizationName: org, jobDescription: desc,
    jobListTimeInMilliseconds: TODAY.getTime() - days * 86400000,
  },
});
const page = (elements, { total = 1000, next = true } = {}) => ({
  paging: { total, links: next ? [{ rel: 'next', href: 'x' }] : [] }, elements,
});
const ok = (body) => ({ ok: true, status: 200, json: async () => body, text: async () => '' });
const bad = (status, text = '') => ({ ok: false, status, json: async () => ({}), text: async () => text });

/** A fake fetch that answers by (keyword, start) and records every URL. */
function fakeFetch(answer) {
  const calls = [];
  let inFlight = 0, maxInFlight = 0;
  const fn = async (url) => {
    const u = new URL(url);
    const call = { keyword: u.searchParams.get('keyword'), start: Number(u.searchParams.get('start')), url };
    calls.push(call);
    inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((r) => setTimeout(r, 2));
    inFlight--;
    return answer(call);
  };
  fn.calls = calls;
  Object.defineProperty(fn, 'maxInFlight', { get: () => maxInFlight });
  return fn;
}
const run = (q, f) => adapter.search(env, { maxResults: 100, ...q }, 100, { fetch: f, today: TODAY });

// --- the request -------------------------------------------------------------

console.log('request shape');

await test('countries use the encoded List() form and nothing sends sortBy', async () => {
  const f = fakeFetch(() => ok(page([], { next: false })));
  await run({ titles: ['Hotel Manager'], countries: ['GB'] }, f);
  const url = f.calls[0].url;
  assert.ok(url.includes('countries=List(urn%3Ali%3Acountry%3Agb)'), url);
  assert.ok(!url.includes('sortBy') && !url.includes('(value:'), url);
  assert.ok(url.includes('q=criteria') && url.includes('keyword=Hotel%20Manager'), url);
});

await test('UK is sent as gb, the code LinkedIn knows', () => {
  assert.ok(encodeQuery(linkedinParams({ countries: ['UK'] }, 0, 24, 'x'))
    .includes('urn%3Ali%3Acountry%3Agb'));
});

await test('the token is sent as a bearer credential and never in the URL', async () => {
  let seenHeaders;
  const f = async (url, init) => { seenHeaders = init.headers; assert.ok(!url.includes('tok')); return ok(page([], { next: false })); };
  await adapter.search(env, { titles: ['x'], maxResults: 5 }, 5, { fetch: f, today: TODAY });
  assert.equal(seenHeaders.Authorization, 'Bearer tok');
  assert.equal(seenHeaders['Linkedin-Version'], '202607');
});

await test('no keyword and no organisation makes no request at all', async () => {
  const f = fakeFetch(() => ok(page([])));
  const r = await run({ countries: ['GB'] }, f);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(r.jobs, []);
});

await test('a missing token is refused, not sent', async () => {
  await assert.rejects(() => adapter.search({}, { titles: ['x'] }, 5, { fetch: fakeFetch(() => ok(page([]))) }),
    (e) => e.code === 'provider_not_configured');
});

// --- one request per title ---------------------------------------------------------

console.log('titles');

await test('each title is its own request set, results merged without repeats', async () => {
  const shared = el('Hotel Manager Shared');
  const f = fakeFetch((c) => ok(page(
    c.keyword === 'Hotel Manager' ? [el('Hotel Manager'), shared] : [el('Resort Manager'), shared],
    { next: false })));
  const r = await run({ titles: ['Hotel Manager', 'Resort Manager'], searchType: 'both' }, f);
  assert.deepEqual([...new Set(f.calls.map((c) => c.keyword))].sort(), ['Hotel Manager', 'Resort Manager']);
  assert.equal(r.jobs.filter((j) => j.title === 'Hotel Manager Shared').length, 1);
  assert.equal(r.jobs.length, 3);
});

await test('at most three titles are searched', () => {
  assert.equal(keywordsFor({ titles: ['a', 'b', 'c', 'd', 'e'] }).length, MAX_KEYWORDS);
});

await test('with no titles the description keywords are the searches', () => {
  assert.deepEqual(keywordsFor({ descriptionKeywords: ['asset', 'fund'] }), ['asset', 'fund']);
});

// --- filters -----------------------------------------------------------------------

console.log('filters');

await test('a keyword that only matched the description is dropped', async () => {
  const f = fakeFetch(() => ok(page([el('Hotel Manager'), el('Supermarket Assistant')], { next: false })));
  const r = await run({ titles: ['Hotel Manager'] }, f);
  assert.deepEqual(r.jobs.map((j) => j.title), ['Hotel Manager']);
  assert.equal(r.scanned, 2);
});

await test('Manager matches Management, in any word order', () => {
  assert.ok(titleMatches('VP - European Hotel Asset Management', ['Hotel Asset Manager']));
  assert.ok(titleMatches('Manager, Hotel', ['Hotel Manager']));
  assert.ok(!titleMatches('Hotel Chef', ['Hotel Manager']));
});

await test('boroughs and districts count as London; commuter towns do not', async () => {
  const locs = ['Croydon, England', 'Canary Wharf, England', 'HA3 0AA, Harrow, England',
                'Watford, England', 'Sutton Coldfield, England'];
  const f = fakeFetch(() => ok(page(locs.map((l) => el('Hotel Manager', { loc: l })), { next: false })));
  const r = await run({ titles: ['Hotel Manager'], cities: ['London'] }, f);
  assert.equal(r.jobs.length, 3);
});

await test('a country-only location is kept and marked, not dropped', async () => {
  const f = fakeFetch(() => ok(page([el('Hotel Manager', { loc: 'United Kingdom' })], { next: false })));
  const r = await run({ titles: ['Hotel Manager'], cities: ['London'] }, f);
  assert.equal(r.jobs.length, 1);
  assert.equal(r.jobs[0].raw_criteria.location_unresolved, true);
});

await test('postings older than the window are dropped', async () => {
  const f = fakeFetch(() => ok(page([el('Hotel Manager', { days: 40 }), el('Hotel Manager Two', { days: 3 })], { next: false })));
  const r = await run({ titles: ['Hotel Manager'], postedWithinDays: 14 }, f);
  assert.deepEqual(r.jobs.map((j) => j.title), ['Hotel Manager Two']);
});

await test('excluded title terms use word boundaries', async () => {
  const f = fakeFetch(() => ok(page([el('Hotel Manager Trainee'), el('Hotel Manager Traineeship X')], { next: false })));
  const r = await run({ titles: ['Hotel Manager'], excludeTitleTerms: ['trainee'] }, f);
  assert.deepEqual(r.jobs.map((j) => j.title), ['Hotel Manager Traineeship X']);
});

await test('description search reads the text; title search with keywords needs one', async () => {
  const rows = () => [el('Supermarket Assistant', { desc: 'We need a Hotel Manager to lead' }), el('Cashier', { desc: 'Tills only' })];
  let r = await run({ titles: ['Hotel Manager'], searchType: 'description' },
    fakeFetch(() => ok(page(rows(), { next: false }))));
  assert.deepEqual(r.jobs.map((j) => j.title), ['Supermarket Assistant']);
  r = await run({ titles: ['Hotel Manager'], descriptionKeywords: ['asset'] },
    fakeFetch(() => ok(page([el('Hotel Manager', { desc: 'asset management' }), el('Hotel Manager B', { desc: 'front desk' })], { next: false }))));
  assert.deepEqual(r.jobs.map((j) => j.title), ['Hotel Manager']);
});

await test('HTML entities in a description do not defeat a keyword match', async () => {
  const f = fakeFetch(() => ok(page([el('Hotel Manager', { desc: 'Food &amp; Beverage asset team' })], { next: false })));
  const r = await run({ titles: ['Hotel Manager'], descriptionKeywords: ['Food & Beverage'] }, f);
  assert.equal(r.jobs.length, 1);
});

await test('rows carry the country tag the app\'s location gate reads', async () => {
  const f = fakeFetch(() => ok(page([el('Hotel Manager')], { next: false })));
  const r = await run({ titles: ['Hotel Manager'], countries: ['gb'] }, f);
  assert.deepEqual(r.jobs[0].raw_criteria.country_codes, ['GB']);
  assert.equal(r.jobs[0].provider, PROVIDER);
});

// --- paging, budget, concurrency -------------------------------------------------------

console.log('paging');

await test('a page one row short with a next link keeps going', async () => {
  const f = fakeFetch((c) => c.start === 0
    ? ok(page(Array.from({ length: 23 }, (_, i) => el(`Hotel Manager ${i}`))))
    : ok(page(Array.from({ length: 24 }, (_, i) => el(`Hotel Manager ${100 + i}`)), { next: false })));
  const r = await run({ titles: ['Hotel Manager'], maxResults: 47 }, f);
  assert.equal(r.jobs.length, 47);
});

await test('no next link ends the scan and is reported as exhausted', async () => {
  const f = fakeFetch(() => ok(page([el('Hotel Manager')], { next: false })));
  const r = await run({ titles: ['Hotel Manager'] }, f);
  assert.equal(r.exhausted, true);
});

await test('the page budget bounds the scan and says the budget ended it', async () => {
  const f = fakeFetch(() => ok(page([el('Cashier')])));      // never matches, never ends
  const r = await run({ titles: ['Hotel Manager'] }, f);
  assert.ok(f.calls.length <= PAGE_BUDGET, `${f.calls.length} > ${PAGE_BUDGET}`);
  assert.equal(r.exhausted, false);
  assert.equal(r.jobs.length, 0);
});

await test('the whole scan stays inside the free-plan subrequest limit with room to spare', () => {
  assert.ok(PAGE_BUDGET + 10 < 50, 'leave room for TheirStack and place lookups');
});

await test('pages run in parallel, bounded, and the scan is fast', async () => {
  const f = fakeFetch(() => ok(page([el('Cashier')])));
  const t0 = Date.now();
  await run({ titles: ['Hotel Manager'] }, f);
  assert.ok(f.maxInFlight > 1, `only ${f.maxInFlight} in flight`);
  assert.ok(f.maxInFlight <= PARALLEL);
  assert.ok(Date.now() - t0 < 500, 'sequential would take PAGE_BUDGET x latency');
});

await test('stops fetching once enough postings are kept', async () => {
  const f = fakeFetch((c) => ok(page(Array.from({ length: PAGE_SIZE }, (_, i) => el(`Hotel Manager ${c.start}-${i}`)))));
  const r = await run({ titles: ['Hotel Manager'], maxResults: 10 }, f);
  assert.equal(r.jobs.length, 10);
  assert.ok(f.calls.length <= PARALLEL, 'one batch is enough');
});

// --- failures ---------------------------------------------------------------------------

console.log('the daily quota');

await test('the default budget is small: it is a slice of one shared daily quota', async () => {
  assert.ok(PAGE_BUDGET <= 12, `${PAGE_BUDGET} pages a search would exhaust a shared quota fast`);
  const f = fakeFetch(() => ok(page([el('Cashier')])));
  await run({ titles: ['Hotel Manager'] }, f);
  assert.ok(f.calls.length <= PAGE_BUDGET);
});

await test('the budget can be raised or lowered by an environment variable, and is capped', async () => {
  for (const [setting, expected] of [['3', 3], ['200', MAX_PAGE_BUDGET], ['0', PAGE_BUDGET], ['abc', PAGE_BUDGET]]) {
    const f = fakeFetch(() => ok(page([el('Cashier')])));
    await adapter.search({ ...env, LINKEDIN_PAGE_BUDGET: setting }, { titles: ['Hotel Manager'], maxResults: 100 }, 100,
      { fetch: f, today: TODAY });
    assert.ok(f.calls.length <= expected, `${setting}: ${f.calls.length} > ${expected}`);
  }
});

await test('the budget is shared between titles, not multiplied by them', async () => {
  const f = fakeFetch(() => ok(page([el('Cashier')])));
  await run({ titles: ['A one', 'B two', 'C three'] }, f);
  assert.ok(f.calls.length <= PAGE_BUDGET, `${f.calls.length} pages for three titles`);
});

await test('a 429 on the first page is rate_limited, not a generic provider error', async () => {
  await assert.rejects(() => run({ titles: ['x'] }, fakeFetch(() => bad(429, '{}'))),
    (e) => e.code === 'rate_limited');
});

await test('a 429 after rows were read keeps them and says the quota ended the scan', async () => {
  const f = fakeFetch((c) => c.start < PARALLEL * PAGE_SIZE ? ok(page([el(`Hotel Manager ${c.start}`)])) : bad(429));
  const r = await adapter.search({ ...env, LINKEDIN_PAGE_BUDGET: '12' }, { titles: ['Hotel Manager'], maxResults: 500 }, 500,
    { fetch: f, today: TODAY });
  assert.ok(r.jobs.length > 0 && r.partial && r.throttled === true);
});

await test("its answers are kept longer than the per-row-billed feed answers", () => {
  assert.equal(adapter.cacheTtl, CACHE_TTL_SECONDS);
  assert.ok(CACHE_TTL_SECONDS > 6 * 3600);
  assert.equal(adapter.quotaLimited, true);
  assert.equal(adapter.billed, false);
});

console.log('a slow LinkedIn must not hold up the search');

await test('every page request carries a timeout signal', async () => {
  const signals = [];
  const f = async (url, init) => { signals.push(init.signal); return ok(page([], { next: false })); };
  await adapter.search(env, { titles: ['x'], maxResults: 5 }, 5, { fetch: f, today: TODAY });
  assert.ok(signals.length > 0 && signals.every((s) => s instanceof AbortSignal));
  assert.ok(PAGE_TIMEOUT_MS <= 15000);
});

await test('a timed-out page surfaces as a failed provider, not a hang', async () => {
  const f = async () => { const e = new Error('timed out'); e.name = 'TimeoutError'; throw e; };
  await assert.rejects(() => adapter.search(env, { titles: ['x'], maxResults: 5 }, 5, { fetch: f, today: TODAY }),
    (e) => e.name === 'TimeoutError');
});

console.log('failures');

await test('an expired token is a loud token_expired', async () => {
  await assert.rejects(() => run({ titles: ['x'] }, fakeFetch(() => bad(401))),
    (e) => e.code === 'token_expired');
});

await test('a failure on the first pages is an error', async () => {
  await assert.rejects(() => run({ titles: ['x'] }, fakeFetch(() => bad(500, '{}'))),
    (e) => e.code === 'provider_error');
});

await test('a failure after rows were read keeps them and says the scan was partial', async () => {
  const f = fakeFetch((c) => c.start < PARALLEL * PAGE_SIZE
    ? ok(page([el(`Hotel Manager ${c.start}`)]))
    : bad(429, 'slow down'));
  const r = await run({ titles: ['Hotel Manager'], maxResults: 500 }, f);
  assert.ok(r.jobs.length > 0);
  assert.ok(r.partial && r.exhausted === false);
});

await test('an expired token mid-scan is still loud, not swallowed as partial', async () => {
  const f = fakeFetch((c) => c.start < PARALLEL * PAGE_SIZE ? ok(page([el(`Hotel Manager ${c.start}`)])) : bad(401));
  await assert.rejects(() => run({ titles: ['Hotel Manager'], maxResults: 500 }, f),
    (e) => e.code === 'token_expired');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
