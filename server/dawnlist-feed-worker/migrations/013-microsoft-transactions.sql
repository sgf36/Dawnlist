-- Migration 013 — Microsoft Store subscriptions, as licences.
--
--   npx wrangler d1 execute dawnlist --remote --file migrations/013-microsoft-transactions.sql
--
-- Apply ONCE, BEFORE deploying the Worker that relies on it. Safe to re-run:
-- it only creates what is absent.
--
-- WHY THIS EXISTS. Paddle declined dawnlist.spencerfields.com on 2026-09-11
-- under "Advertising and Marketing/Job Boards". An appeal is open and Paddle
-- billing is cheaper, so the Paddle path is not being replaced — the Microsoft
-- Store's own subscription is a second till, running alongside it, and this is
-- where its purchases land.
--
-- A SEPARATE TABLE FROM apple_transactions, DELIBERATELY. The two stores
-- identify a purchase differently — Apple by an original transaction id,
-- Microsoft by a per-user product id from the collections API — and there is
-- no shared key that means the same thing in both. One table with half its
-- columns null depending on the row is a table nobody can query safely.
--
-- THE LICENCE IS THE SHARED PART, AND THAT IS THE POINT. Whatever issued it,
-- a licence is a licence: same table, same caps, same metering. Everything
-- downstream of here — the feed, the admin console, the usage counters — never
-- learns which store paid. That is what makes returning to Paddle a build
-- variant rather than a migration, and it is why this table holds the purchase
-- and not the entitlement.
--
-- user_id IS MICROSOFT'S ANONYMOUS ID FOR THE CUSTOMER, not an account name.
-- The collections API answers per user without telling us who they are, which
-- is the whole of what we need and less than we could have asked for.

CREATE TABLE IF NOT EXISTS microsoft_transactions (
    user_id     TEXT PRIMARY KEY,
    licence_key TEXT NOT NULL UNIQUE,
    product_id  TEXT,
    status      TEXT NOT NULL,
    expires_at  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
