-- Migration 010 — Mac App Store purchases, one licence each.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/010-apple-transactions.sql
--
-- Apply ONCE, before deploying the Worker that serves /v1/apple: the route
-- writes to this table on its first request, and a missing table answers 500,
-- which the Mac app reads as "unreachable" — every subscriber would run on
-- grace until it expired, with nothing saying why.
--
-- WHY A TABLE AND NOT A COLUMN ON `licences`. The primary key is what makes one
-- Apple purchase map to exactly one licence, and the UNIQUE licence key is what
-- stops one licence being claimed by two purchases. As a nullable column on
-- `licences` neither holds: SQLite lets any number of rows share a NULL, and a
-- column UNIQUE there would have to be added by rebuilding the whole table.
--
-- No foreign key to `licences`, deliberately: `apple.js` writes this row FIRST
-- so that two racing exchanges converge on one licence key, and a foreign key
-- would force the opposite order.

CREATE TABLE IF NOT EXISTS apple_transactions (
    original_transaction_id TEXT PRIMARY KEY,
    licence_key             TEXT NOT NULL UNIQUE,
    environment             TEXT,
    status                  TEXT NOT NULL,
    expires_at              TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
