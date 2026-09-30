/**
 * The Worker's place matcher against the SAME cases the Python one is tested on.
 * Run: node test/places.test.mjs
 *
 * places.js is a port of app/core/places.py, and its tables are generated from
 * it. Both are checked against tests/fixtures/place_cases.json, so one cannot
 * drift from the other unnoticed.
 */
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { locationMatches } from '../src/places.js';

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const cases = JSON.parse(readFileSync(
  new URL('../../../tests/fixtures/place_cases.json', import.meta.url), 'utf8'));

test('the shared fixture is not empty', () => assert.ok(cases.length >= 30));

for (const c of cases) {
  test(`${JSON.stringify(c.locations[0])} in ${JSON.stringify(c.cities)} -> ${c.expected}`, () => {
    assert.strictEqual(locationMatches(c.locations, c.cities), c.expected);
  });
}

test('an unknown place is null (keep and mark), never false', () => {
  assert.strictEqual(locationMatches(['United Kingdom'], ['London']), null);
});

test('commuter towns and namesakes are outside London', () => {
  for (const l of ['Watford, England', 'Slough, England', 'Sutton Coldfield, England',
                   'Kingston Upon Hull, England', 'Brentwood, England']) {
    assert.strictEqual(locationMatches([l], ['London']), false, l);
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
