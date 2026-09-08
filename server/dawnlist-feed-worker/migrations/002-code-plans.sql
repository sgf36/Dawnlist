-- Migration 002 — override codes carry a plan.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/002-code-plans.sql
--
-- Apply ONCE. See 001 for why these live outside schema.sql.
--
-- WHAT THIS FIXES, and it is the same fault as 001 in a second place.
-- `handleRedeem` minted a licence and set `tier`, and never wrote the cap
-- columns — so every redeemed code, including one meant as a short trial, got
-- the Worker's full 700/day anti-abuse default. A trial was therefore
-- indistinguishable from a paid subscription in the only place that decides
-- what a licence may actually do.
--
-- That is what made a card-free trial unofferable: not the absence of a plan
-- (PLANS.trial has existed since plans were written) but the absence of any
-- route to issue one.

ALTER TABLE codes ADD COLUMN plan TEXT;

-- Existing codes keep behaving exactly as they do now. They were issued
-- against the 700/day default and changing that retroactively would cut off
-- somebody mid-use, which is not a migration's job.
UPDATE codes SET plan = 'standard' WHERE plan IS NULL;
