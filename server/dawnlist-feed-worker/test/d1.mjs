/**
 * A D1 binding backed by a real SQLite engine, for tests.
 *
 * WHY NOT A STUB. The faults this Worker has had are SQL-shaped: a cap read
 * and written in separate statements, a redemption counted and inserted in
 * separate statements, a schema file that no longer matched the migrations. A
 * stub that pattern-matches SQL text answers whatever the test author believed
 * the SQL did, so it passes exactly the bugs it should catch.
 *
 * WHY EVERY CALL YIELDS FIRST. On D1 every statement is a network round trip,
 * so two requests to one Worker interleave between statements. node:sqlite is
 * synchronous and would otherwise run each request to completion, hiding every
 * race. A `batch` yields once and then runs as one transaction, which is what
 * D1 promises for a batch.
 *
 * Binding mirrors D1's refusals: `undefined` is an error (D1_TYPE_ERROR), and
 * booleans become 1/0.
 */
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';

const ROOT = new URL('../', import.meta.url);

export const SCHEMA = readFileSync(new URL('schema.sql', ROOT), 'utf8');

/** Migration files in the order they are applied: by filename. */
export function migrations() {
  return readdirSync(new URL('migrations/', ROOT))
    .filter((f) => f.endsWith('.sql'))
    .sort()
    .map((name) => ({ name, sql: readFileSync(new URL(`migrations/${name}`, ROOT), 'utf8') }));
}

export function fixture(name) {
  return readFileSync(new URL(`test/fixtures/${name}`, ROOT), 'utf8');
}

const turn = () => new Promise((resolve) => setImmediate(resolve));

function bindable(value, i) {
  if (value === undefined) {
    throw new TypeError(`D1_TYPE_ERROR: parameter ?${i + 1} is undefined`);
  }
  if (typeof value === 'boolean') return value ? 1 : 0;
  return value;
}

export function makeD1({ sql = SCHEMA } = {}) {
  const db = new DatabaseSync(':memory:');
  if (sql) db.exec(sql);

  const totalChanges = () => Number(db.prepare('SELECT total_changes() AS n').get().n);

  function execute(stmt) {
    const params = stmt.params.map(bindable);
    const before = totalChanges();
    const results = db.prepare(stmt.sql).all(...params).map((r) => ({ ...r }));
    return { results, success: true, meta: { changes: totalChanges() - before } };
  }

  function statement(sqlText, params = []) {
    return {
      sql: sqlText,
      params,
      bind(...args) { return statement(sqlText, args); },
      async first(column) {
        await turn();
        const row = execute(this).results[0] ?? null;
        return column === undefined ? row : (row ? row[column] ?? null : null);
      },
      async all() { await turn(); return execute(this); },
      async run() { await turn(); return execute(this); },
    };
  }

  return {
    prepare: (sqlText) => statement(sqlText),
    async batch(statements) {
      await turn();
      db.exec('BEGIN');
      try {
        const out = statements.map(execute);
        db.exec('COMMIT');
        return out;
      } catch (err) {
        db.exec('ROLLBACK');
        throw err;
      }
    },
    async exec(sqlText) { await turn(); db.exec(sqlText); },
    /** Synchronous read for assertions; never used by the code under test. */
    query(sqlText, ...args) {
      return db.prepare(sqlText).all(...args.map(bindable)).map((r) => ({ ...r }));
    },
    sqlite: db,
  };
}
