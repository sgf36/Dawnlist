/**
 * The page size actually asked for.
 *
 * The Worker read `q.limit`; the application sends `maxResults`. So the user's
 * own "how many postings do you want" setting was received and discarded, and
 * every search fell back to the 100-row page cap. The daily cap still bounded
 * the damage, which is why nothing ever looked wrong — but on a feed billed per
 * row RETURNED, a control that quietly does nothing is the expensive kind.
 *
 * These tests are named for the money rather than for the field.
 */
import assert from 'node:assert';

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

// The clamp, lifted verbatim from src/index.js. Kept as a copy deliberately:
// the real one is inside a fetch-making adapter, and a test that had to stub
// the network to check an arithmetic expression would prove less, not more.
const MAX_PAGE = 100;
const chosen = (q, headroom) =>
  Math.max(1, Math.min(q.maxResults ?? q.limit ?? MAX_PAGE, MAX_PAGE, headroom));

console.log('page size');

test('the number the APP sends is the number used', () => {
  assert.equal(chosen({ maxResults: 20 }, 700), 20,
    'maxResults is the name app/feed/managed.py actually sends');
});

test('asking for twenty does not buy a hundred', () => {
  assert.notEqual(chosen({ maxResults: 20 }, 700), MAX_PAGE);
});

test('a hand-made request using `limit` still works', () => {
  assert.equal(chosen({ limit: 5 }, 700), 5);
});

test('maxResults wins when both are present', () => {
  assert.equal(chosen({ maxResults: 10, limit: 90 }, 700), 10);
});

test('neither given falls back to the page cap', () => {
  assert.equal(chosen({}, 700), MAX_PAGE);
});

test('headroom always wins, so a cap cannot be overshot', () => {
  assert.equal(chosen({ maxResults: 100 }, 3), 3);
});

test('a spent allowance still fetches at least one row, never zero or less', () => {
  // Math.min with a headroom of 0 would otherwise ask the provider for 0 rows,
  // which is a wasted round trip rather than a refusal. The cap check upstream
  // is what stops a spent licence; this only keeps the arithmetic sane.
  assert.equal(chosen({ maxResults: 50 }, 0), 1);
});

test('a nonsense zero from a client does not become the page cap', () => {
  // `q.limit || MAX_PAGE` turned 0 into 100. `??` does not.
  assert.equal(chosen({ maxResults: 0 }, 700), 1);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
