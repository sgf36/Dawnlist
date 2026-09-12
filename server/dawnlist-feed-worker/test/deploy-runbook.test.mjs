// The runbook is a SECOND COPY of facts that live in migrations/ and src/, and
// an unprotected second copy goes stale without announcing it. The failure is
// worse than useless: somebody follows a runbook that omits migration 011,
// deploys a Worker that reads a column nobody created, and the runbook is the
// last place they will look for the cause.
//
// These tests do not check that the prose is right. They check that every file
// and every credential the deploy actually depends on is NAMED in it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const runbook = readFileSync(new URL('../DEPLOY-RUNBOOK.md', import.meta.url), 'utf8');

test('every migration on disk is named in the runbook', () => {
  const migrations = readdirSync(new URL('../migrations', import.meta.url))
    .filter((f) => f.endsWith('.sql'));
  assert.ok(migrations.length >= 10, 'expected the migrations directory to be found');
  const missing = migrations.filter((f) => !runbook.includes(f));
  assert.deepEqual(missing, [], 'migrations missing from DEPLOY-RUNBOOK.md');
});

test('positive control: a migration that does not exist is not named either', () => {
  // Proves the test above can fail. If the runbook mentioned every plausible
  // filename — a glob, a wildcard — the check would pass vacuously.
  assert.ok(!runbook.includes('099-not-a-real-migration.sql'));
});

test('every credential the code reads is named in the runbook', () => {
  // wrangler.jsonc's comment block lists these too, but a deploy is followed
  // from the runbook, and a secret that only the config file mentions is a
  // secret nobody sets.
  const sources = ['src/apple.js', 'src/codes.js', 'src/index.js', 'src/paddle.js'];
  const read = new Set();
  for (const file of sources) {
    const text = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
    for (const m of text.matchAll(/env\.([A-Z][A-Z0-9_]{3,})/g)) read.add(m[1]);
  }
  // DB is the binding, not a credential; the vars are in wrangler.jsonc and
  // ship with the deploy, so nobody has to be told to set them.
  for (const notACredential of ['DB', 'APPLE_BUNDLE_ID', 'APPLE_PRODUCT_ID',
                                'PADDLE_PRICE_STANDARD', 'PADDLE_PRICE_GLOBAL']) {
    read.delete(notACredential);
  }
  assert.ok(read.has('APPLE_SHARED_SECRET'), 'expected the Apple secrets to be found');
  const missing = [...read].filter((name) => !runbook.includes(name)).sort();
  assert.deepEqual(missing, [], 'credentials missing from DEPLOY-RUNBOOK.md');
});

test('every Paddle event the webhook handles is listed for the destination', () => {
  // An event the destination does not send never arrives. The refund handling
  // and the ordering guard then go quiet, and nothing reports a gap.
  const paddle = readFileSync(new URL('../src/paddle.js', import.meta.url), 'utf8');
  const events = new Set();
  for (const m of paddle.matchAll(/'((?:transaction|subscription|adjustment)\.[a-z_]+)'/g)) {
    events.add(m[1]);
  }
  assert.ok(events.size >= 10, 'expected the handled events to be found');
  const missing = [...events].filter((e) => !runbook.includes(e)).sort();
  assert.deepEqual(missing, [], 'events missing from DEPLOY-RUNBOOK.md');
});
