-- Migration 008 — failed code attempts counted against a hash, not an address.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/008-hashed-code-attempts.sql
--
-- Apply IMMEDIATELY BEFORE deploying the Worker that relies on it. The Worker
-- deployed today reads and writes `ip`, so every redemption between this
-- migration and that deploy fails. A second run deletes the day's counts again
-- and then stops at "no such column: ip".
--
-- WHAT THIS FIXES. code_attempts held the connecting IP address of every failed
-- redemption, and nothing ever deleted a row. The Worker now stores a keyed
-- hash (CLIENT_HASH_SECRET, or a salted SHA-256 when that is unset) and a cron
-- trigger deletes rows older than two days.
--
-- The existing rows are DELETED rather than converted: raw addresses are what
-- is being removed, and a row only ever mattered on the day it was written.
-- A connection locked out today gets a fresh allowance.

DELETE FROM code_attempts;
ALTER TABLE code_attempts RENAME COLUMN ip TO client_hash;
