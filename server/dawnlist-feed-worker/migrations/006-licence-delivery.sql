-- Migration 006 — whether each licence email was delivered, and if not, why.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/006-licence-delivery.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. SQLite has no
-- `ADD COLUMN IF NOT EXISTS`, so a second run stops at "duplicate column name"
-- and changes nothing.
--
-- WHAT THIS FIXES. A licence whose email failed left one trace: a log line,
-- kept for days, that nobody reads unless a customer complains. The outcome is
-- now on the licence row — 'pending' from issue, then 'sent', 'failed' or
-- 'skipped' — with a failure CODE (never an address or a message), and
-- GET /admin/undelivered lists every Paddle licence still pending or failed.
--
-- Existing licences stay NULL: whether their email arrived is not recorded
-- anywhere this migration could read it from.

ALTER TABLE licences ADD COLUMN delivery_status TEXT;
ALTER TABLE licences ADD COLUMN delivery_error_code TEXT;
