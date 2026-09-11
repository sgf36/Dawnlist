/**
 * Rows the tests need to exist before a request, written with the same columns
 * the Worker itself writes, so a seeded row cannot pass where a real one fails.
 */
import { newCode, normalise } from '../src/codes.js';

export function seedCode(d1, over = {}) {
  const c = {
    code: newCode(), note: 'test', max_uses: 1, revoked: 0,
    created_at: new Date().toISOString(), expires_at: null, role: 'byo', plan: 'standard',
    ...over,
  };
  d1.sqlite.prepare(
    `INSERT INTO codes (code, note, max_uses, revoked, created_at, expires_at, role, plan,
                        code_normalised)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(c.code, c.note, c.max_uses, c.revoked, c.created_at, c.expires_at, c.role, c.plan,
        normalise(c.code));
  return c;
}
