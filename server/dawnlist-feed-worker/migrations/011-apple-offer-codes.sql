-- Migration 011 — the Mac subscription's offer codes, and who they went to.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/011-apple-offer-codes.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. Safe to re-run:
-- it only creates what is absent.
--
-- WHY THIS EXISTS. Dawnlist's own override codes were removed from the Mac
-- build under App Store guideline 3.1.1, so nothing in the app can unlock a
-- macOS copy. An App Store offer code is the only remaining way to give
-- somebody free access to the Mac subscription — and offer codes are minted in
-- App Store Connect, which needs an Apple credential that must never sit in
-- this Worker. An individual App Store Connect key still carries App Manager
-- rights over the WHOLE app: edit the listing, change pricing, submit builds.
-- Apple has no finer grain.
--
-- So the split is deliberate: codes are minted on a trusted machine with that
-- key, and only the resulting strings are uploaded here. This table is a
-- ledger of who was given what, nothing more, and it holds no Apple
-- credential of any kind.
--
-- THERE IS NO redeemed_at COLUMN, AND THAT IS NOT AN OVERSIGHT. A redeemed
-- offer code produces a StoreKit transaction carrying `offerType` and
-- `offerIdentifier` — the OFFER's reference name, shared by every code in the
-- batch. The individual code is not in the transaction and Apple does not
-- report it, so a per-code "redeemed" flag could only ever be filled in by
-- hand, and a field maintained by memory is a field that lies. Redemptions are
-- counted per BATCH instead, in apple_offer_batches below, which the Worker
-- can fill in truthfully from what a transaction actually says.

CREATE TABLE IF NOT EXISTS apple_offer_codes (
    code        TEXT PRIMARY KEY,
    batch       TEXT NOT NULL,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_to TEXT,
    assigned_at TEXT,
    note        TEXT,
    void        INTEGER NOT NULL DEFAULT 0
);

-- The index the "give me an unused one" query runs down. Without it that
-- query is a table scan of every code ever minted, which is fine at ten and
-- not at Apple's limit of twenty-five thousand.
CREATE INDEX IF NOT EXISTS apple_offer_codes_free
    ON apple_offer_codes (void, assigned_at, added_at);

-- Per-batch redemption counts, incremented when /v1/apple settles a
-- transaction that came from an offer code. "3 of 10 in friends-2026 have
-- been used" is a true statement; "Sean used code ABC" is not one we can make.
CREATE TABLE IF NOT EXISTS apple_offer_batches (
    batch       TEXT PRIMARY KEY,
    offer_id    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    redemptions INTEGER NOT NULL DEFAULT 0
);
