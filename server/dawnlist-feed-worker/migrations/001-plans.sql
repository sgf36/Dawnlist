-- Migration 001 — per-licence plans.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/001-plans.sql
--
-- Apply ONCE. SQLite has no `ADD COLUMN IF NOT EXISTS`, so a second run fails
-- with "duplicate column name" — which is noisy but harmless, and is the
-- reason this lives here rather than being appended to `schema.sql`. Re-running
-- the whole schema after this point would fail on these same two lines and stop
-- before anything after them.
--
-- WHAT THIS FIXES. The `licences` table has carried `max_postings_per_day`,
-- `max_refreshes_per_day` and `max_saved_queries` from the beginning, and the
-- Paddle webhook never wrote to any of them. Every licence therefore fell back
-- to the Worker's 700/day anti-abuse default, so a customer on a larger plan
-- received exactly the same allowance as one on a smaller plan. A set of
-- columns read on every request and written by nothing is the blind spot the
-- wiring audit exists to find.
--
-- The two columns below do not enforce anything. The CAPS are the enforced
-- truth; these record which plan set them, so support can answer "what am I
-- paying for?" without reverse-engineering it from three numbers.

-- Which plan the licence is on. Nullable deliberately: licences issued before
-- plans existed, and those minted by an override code, genuinely have no
-- answer, and writing 'standard' for them would assert something nobody
-- verified.
ALTER TABLE licences ADD COLUMN plan TEXT;

-- Set to 1 when a Paddle event's price id matched nothing configured, so the
-- licence took the fallback plan. These rows are customers who may be on the
-- wrong caps, and this flag is the only way to find them later: the webhook
-- logs counts, never payloads, so the event itself is gone.
--
--   SELECT licence_key, plan FROM licences WHERE plan_unmatched = 1;
--
ALTER TABLE licences ADD COLUMN plan_unmatched INTEGER NOT NULL DEFAULT 0;

-- Backfill: every existing licence keeps the behaviour it already had. They
-- are on the Worker default (700/day) because nothing ever wrote a cap, so
-- naming them 'standard' — whose ceiling IS 700 — changes nothing about what
-- they can do, and makes them legible.
--
-- `plan_unmatched` stays 0 for these: they did not fall back from a failed
-- price lookup, they predate plans entirely, which is a different thing and
-- should not appear in the report above.
UPDATE licences
   SET plan = 'standard',
       max_postings_per_day  = COALESCE(max_postings_per_day, 700),
       max_refreshes_per_day = COALESCE(max_refreshes_per_day, 3),
       max_saved_queries     = COALESCE(max_saved_queries, 10)
 WHERE plan IS NULL;
