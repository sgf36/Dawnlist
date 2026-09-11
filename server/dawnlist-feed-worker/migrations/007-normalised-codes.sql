-- Migration 007 — codes found by an indexed, normalised column.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/007-normalised-codes.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. SQLite has no
-- `ADD COLUMN IF NOT EXISTS`, so a second run stops at "duplicate column name"
-- and changes nothing.
--
-- WHAT THIS FIXES. Redeeming a code read EVERY code into the Worker and
-- compared each one in JavaScript, so that "dl abcd efgh" matched
-- "DL-ABCD-EFGH". On a public route that anybody can call, the cost of one
-- guess grew with every code ever minted. The normalised form is now stored
-- and indexed, and a redemption reads one row.
--
-- The backfill mirrors normalise() in src/codes.js — upper case, with anything
-- that is not A-Z or 0-9 removed — for codes made of letters, digits, hyphens
-- and spaces, which is every code newCode() generates.
--
-- CHECK FIRST. Both must return no rows, or the backfill differs from
-- normalise() for some code, or the unique index cannot be built:
--
--   SELECT code FROM codes WHERE code GLOB '*[^A-Za-z0-9 -]*';
--
--   SELECT UPPER(REPLACE(REPLACE(code, '-', ''), ' ', '')) AS n, COUNT(*)
--     FROM codes GROUP BY n HAVING COUNT(*) > 1;

ALTER TABLE codes ADD COLUMN code_normalised TEXT;

UPDATE codes
   SET code_normalised = UPPER(REPLACE(REPLACE(code, '-', ''), ' ', ''))
 WHERE code_normalised IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS codes_by_normalised ON codes (code_normalised);
