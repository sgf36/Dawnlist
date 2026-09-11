-- Migration 009 — a record of what the admin console was used to do.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/009-admin-audit.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. Safe to re-run:
-- it only creates what is absent.
--
-- WHAT THIS FIXES. The console can mint codes, revoke them and resend licence
-- keys, and nothing recorded that it had. A stolen or leaked administrator
-- licence could be used and leave no trace. Every handled admin request now
-- writes one row: the action, a SHA-256 of the administrator's licence key
-- (never the key), the target masked to its last four characters, and the
-- time. No notes, addresses or bodies. The same rows bound how many requests
-- one administrator may make in ten minutes.

CREATE TABLE IF NOT EXISTS admin_audit (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL DEFAULT (datetime('now')),
    action     TEXT NOT NULL,
    actor_hash TEXT NOT NULL,
    target     TEXT
);
CREATE INDEX IF NOT EXISTS admin_audit_by_actor ON admin_audit (actor_hash, at);
