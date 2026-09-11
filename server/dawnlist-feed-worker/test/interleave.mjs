/**
 * Forcing two requests to overlap at the worst moment, for race tests.
 *
 * The scheduler often runs two parallel requests one after the other, and a
 * race test that depends on it happening to interleave them passes against the
 * very code it was written to catch. That happened once in this suite.
 */

/**
 * A D1 that holds statements matching `pattern` until `parties` of them are
 * waiting, then releases them together. A batch is held if any statement in it
 * matches.
 */
export function barrier(d1, pattern, parties = 2) {
  let waiting = [];
  const release = () => { const go = waiting; waiting = []; go.forEach((resolve) => resolve()); };
  const gate = () => new Promise((resolve) => {
    waiting.push(resolve);
    if (waiting.length === parties) release();
    // A regression that stops a party reaching the statement must fail the
    // test, not hang it.
    else setTimeout(release, 500);
  });
  const wrap = (stmt) => ({
    ...stmt,
    bind: (...args) => wrap(stmt.bind(...args)),
    first: async (col) => { if (pattern.test(stmt.sql)) await gate(); return stmt.first(col); },
    run: async () => { if (pattern.test(stmt.sql)) await gate(); return stmt.run(); },
    all: async () => { if (pattern.test(stmt.sql)) await gate(); return stmt.all(); },
  });
  return {
    ...d1,
    prepare: (sql) => wrap(d1.prepare(sql)),
    batch: async (stmts) => {
      if (stmts.some((s) => pattern.test(s.sql))) await gate();
      return d1.batch(stmts);
    },
  };
}
