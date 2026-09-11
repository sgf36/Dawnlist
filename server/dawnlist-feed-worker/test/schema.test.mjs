/**
 * The schema file, against a real SQLite engine.
 * Run: node test/schema.test.mjs
 *
 * What these protect, in one line each:
 *   - schema.sql installs a working database, and can be run twice;
 *   - the live database, moved forward by every migration, ends up with the
 *     same tables, columns and indexes as a fresh install;
 *   - the inserts the Worker actually performs succeed on BOTH of them.
 *
 * The live database was created from schema.sql as it stood on 2026-09-06
 * (test/fixtures/schema-as-deployed-2026-09-06.sql, taken from git at 30d3fb3)
 * and has had migrations applied since. Comparing a fresh install with that
 * reconstruction is the only way a drift between the two is ever noticed
 * before a deploy: neither file fails on its own.
 */
import assert from 'node:assert';
import { SCHEMA, fixture, makeD1, migrations } from './d1.mjs';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const DEPLOYED = fixture('schema-as-deployed-2026-09-06.sql');

/** The live database as it is today: the deployed schema plus every migration. */
function migratedDB() {
  const d1 = makeD1({ sql: DEPLOYED });
  for (const m of migrations()) {
    try {
      d1.sqlite.exec(m.sql);
    } catch (err) {
      throw new Error(`migration ${m.name} failed: ${err.message}`);
    }
  }
  return d1;
}

/**
 * Columns the live database carries and a fresh install deliberately does not:
 * the token meters of the inference proxy removed on 2026-09-06. Named one by
 * one, so any OTHER difference still fails.
 */
const LEGACY_ONLY = new Set([
  'usage_daily.input_tokens', 'usage_daily.output_tokens', 'licences.max_tokens_per_day',
]);

function shape(d1) {
  const tables = d1.query(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
  const out = { columns: {}, indexes: {} };
  for (const { name } of tables) {
    for (const c of d1.query(`PRAGMA table_info(${name})`)) {
      const key = `${name}.${c.name}`;
      if (LEGACY_ONLY.has(key)) continue;
      out.columns[key] = { type: c.type, notnull: c.notnull, dflt: c.dflt_value, pk: c.pk };
    }
    for (const ix of d1.query(`PRAGMA index_list(${name})`)) {
      // Auto-indexes are named by position and differ between a table created
      // whole and one built up by ALTER, while describing the same constraint.
      const cols = d1.query(`PRAGMA index_info(${ix.name})`).map((c) => c.name).join(',');
      const label = ix.origin === 'pk' ? `${name}:pk` : ix.name;
      out.indexes[label] = { unique: ix.unique, cols, partial: ix.partial };
    }
  }
  return out;
}

console.log('schema.sql');

await test('a fresh install applies cleanly', async () => {
  const d1 = makeD1();
  assert.ok(d1.query("SELECT 1 FROM sqlite_master WHERE name = 'licences'").length);
});

await test('it can be run twice without failing', async () => {
  const d1 = makeD1();
  d1.sqlite.exec(SCHEMA);
  assert.equal(d1.query('SELECT COUNT(*) AS n FROM providers')[0].n, 1,
    'the seed row is not duplicated either');
});

await test('it carries no ALTER TABLE, which is what stopped a second run', async () => {
  assert.ok(!/^\s*ALTER\s+TABLE/im.test(SCHEMA));
});

await test('the codes table has its plan column', async () => {
  const cols = makeD1().query('PRAGMA table_info(codes)').map((c) => c.name);
  assert.ok(cols.includes('plan'));
});

await test('the removed inference token columns are not created', async () => {
  const d1 = makeD1();
  const usage = d1.query('PRAGMA table_info(usage_daily)').map((c) => c.name);
  const lic = d1.query('PRAGMA table_info(licences)').map((c) => c.name);
  assert.ok(!usage.includes('input_tokens') && !usage.includes('output_tokens'));
  assert.ok(!lic.includes('max_tokens_per_day'));
  // Positive control: the table itself and a real column are there.
  assert.ok(usage.includes('postings'));
});

console.log('\nmigrations');

await test('every migration applies to the database as deployed', async () => {
  migratedDB();
});

await test('the migrated live database matches a fresh install', async () => {
  const fresh = shape(makeD1());
  const live = shape(migratedDB());
  assert.deepEqual(live.columns, fresh.columns);
  assert.deepEqual(live.indexes, fresh.indexes);
});

await test('positive control: a column missing from schema.sql IS detected', async () => {
  const live = migratedDB();
  live.sqlite.exec('ALTER TABLE licences ADD COLUMN drift_probe TEXT');
  assert.throws(() => assert.deepEqual(shape(live).columns, shape(makeD1()).columns));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
