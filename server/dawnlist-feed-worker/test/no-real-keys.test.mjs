/**
 * No real licence key in any test file.
 * Run: node test/no-real-keys.test.mjs
 *
 * A production admin licence key was found in test/email.test.mjs. A licence
 * key IS the credential — the admin console authorises on nothing else — so a
 * key in a test is a key in every clone, fork and paste of the repository.
 *
 * The check is on the SHAPE newLicenceKey() produces, not on a list of known
 * keys, because the next leaked key will not be on any list.
 */
import assert from 'node:assert';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { newLicenceKey } from '../src/paddle.js';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const KEY_SHAPE = /DAWN(?:-[0-9A-Z]{8}){4}/g;
const FAKE = 'DAWN-TEST0000-TEST0000-TEST0000-TEST0000';

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = new URL(name, dir);
    return statSync(path).isDirectory() ? files(new URL(`${name}/`, dir)) : [path];
  });
}

function realKeysIn(text) {
  return (text.match(KEY_SHAPE) || []).filter((k) => k !== FAKE);
}

console.log('licence keys in tests');

await test('no test file contains a real-shaped licence key', async () => {
  const offenders = files(new URL('./', import.meta.url))
    .filter((path) => realKeysIn(readFileSync(path, 'utf8')).length)
    .map((path) => path.pathname.split('/').pop());
  assert.deepEqual(offenders, [], `key-shaped strings found in: ${offenders.join(', ')}`);
});

await test('positive control: a generated key IS caught', async () => {
  assert.equal(realKeysIn(`const k = '${newLicenceKey()}';`).length, 1);
});

await test('positive control: the fake has the real shape, so allowing it is a choice', async () => {
  assert.equal((FAKE.match(KEY_SHAPE) || []).length, 1);
  assert.equal(realKeysIn(FAKE).length, 0);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
