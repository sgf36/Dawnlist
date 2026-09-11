-- Migration 003 — one licence per Paddle subscription.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/003-one-licence-per-subscription.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it.
--
-- WHAT THIS FIXES. The webhook looked for a licence and inserted one if none
-- was found, in two statements. subscription.created and transaction.completed
-- for one purchase are different events, so the idempotency table never
-- stopped them, and delivered together both found nothing and both inserted:
-- two keys and two emails for one payment, with the meter split across them.
-- With this index the database refuses the second row, and the webhook's
-- INSERT ... ON CONFLICT DO NOTHING RETURNING tells it that it lost the race.
--
-- NULLs are distinct in a SQLite unique index, so licences minted from codes,
-- which have no subscription, are unaffected.
--
-- CHECK FIRST. The index cannot be created while duplicates exist; the
-- statement then fails and changes nothing. This must return no rows:
--
--   SELECT paddle_subscription_id, COUNT(*) FROM licences
--    WHERE paddle_subscription_id IS NOT NULL
--    GROUP BY paddle_subscription_id HAVING COUNT(*) > 1;
--
-- If it returns any, find out which key the customer is using (usage_daily
-- shows it), expire the others, and clear their paddle_subscription_id, before
-- applying this.

CREATE UNIQUE INDEX IF NOT EXISTS licences_by_subscription
    ON licences (paddle_subscription_id);
