-- Migration 004 — the newest known state of each Paddle subscription.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/004-subscription-state.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. Safe to re-run:
-- it only creates a table if absent.
--
-- WHAT THIS FIXES. Paddle does not promise to deliver events in order, and a
-- retry reorders them freely. The webhook applied whatever arrived last, so an
-- older "canceled" delivered after a newer "resumed" switched off a paying
-- customer, and a "subscription.created" retried after a cancellation issued a
-- working licence for a subscription that had already ended. Each event is now
-- applied only if it is at least as new as `last_event_at`, and the licence's
-- status is copied from `licence_status` here.
--
-- No backfill. Existing licences have no row until their next event, and an
-- event with nothing recorded before it is applied as it always was.

CREATE TABLE IF NOT EXISTS paddle_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    last_event_at   TEXT,
    paddle_status   TEXT,
    licence_status  TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
