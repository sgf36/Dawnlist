-- Migration 005 — which subscription each completed payment was for.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/005-paddle-transactions.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. Safe to re-run:
-- it only creates a table if absent.
--
-- WHAT THIS FIXES. A refund or a chargeback left the licence it paid for
-- working indefinitely: the webhook ignored adjustment events. Paddle's
-- adjustment names the TRANSACTION it reverses, and only sometimes the
-- subscription, so the webhook now records transaction -> subscription when a
-- payment completes and uses it to find the licence a refund has to end.
--
-- No backfill is possible from this database. A refund for a payment made
-- before this is applied falls back to the adjustment's own subscription_id.

CREATE TABLE IF NOT EXISTS paddle_transactions (
    transaction_id  TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
