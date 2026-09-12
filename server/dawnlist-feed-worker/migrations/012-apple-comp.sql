-- Migration 012 — comp access for Mac users who never paid, and only them.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/012-apple-comp.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. These are ALTER
-- TABLE ADD COLUMN and SQLite has no "IF NOT EXISTS" for them, so a second run
-- stops at "duplicate column name" and changes nothing. That is a safe
-- failure, not a broken database — the same note as 006 and 007.
--
-- WHAT THIS IS FOR. An App Store offer code grants a fixed free period and
-- then Apple stops answering yes. For somebody who is meant to have the app
-- indefinitely — family, a long-term tester — that means re-sending a code
-- they must redeem again. The Worker already decides what /v1/apple returns,
-- so it can go on returning a licence after Apple's period ends.
--
-- THE DANGER THAT CREATES, AND THE TWO GATES THAT CLOSE IT. Done carelessly
-- this hands free access to a PAYING customer whose subscription lapsed, which
-- is the opposite of the intent. So comp requires BOTH:
--
--   1. EVIDENCE, which nobody here can fabricate. `offer_identifier` is read
--      out of the transaction payload Apple SIGNED, and says which offer the
--      subscription began with. A customer who paid has no comp offer on their
--      transaction, so they can never qualify — not because we remembered to
--      exclude them, but because the evidence does not exist.
--   2. INTENT. An administrator sets `comp` deliberately, per person.
--
-- Neither alone does anything. Gate 1 alone would comp everyone who ever
-- redeemed any code; gate 2 alone is a switch that could be flipped on a
-- paying customer by mistake.
--
-- `offer_type` is recorded but NOTHING DEPENDS ON IT. It is an integer enum
-- whose value for an offer-code redemption could not be confirmed from Apple's
-- documentation, so keying a gate on it would be resting a guard on a guess.
-- It is stored so the first real redemption tells us what it actually is.

ALTER TABLE apple_transactions ADD COLUMN offer_identifier TEXT;
ALTER TABLE apple_transactions ADD COLUMN offer_type INTEGER;
ALTER TABLE apple_transactions ADD COLUMN comp INTEGER NOT NULL DEFAULT 0;
