-- Dawnlist feed Worker — D1 schema, for a FRESH database only.
--
-- Metering only. This database holds counts, licences and provider flags; it
-- never holds job descriptions, CVs, queries with personal text, or anything
-- else that would make the store privacy labels a lie.
--
-- FRESH INSTALL vs EXISTING DATABASE. This file is the whole schema as it
-- stands after every file in migrations/, and it is safe to run twice: every
-- statement is IF NOT EXISTS or ON CONFLICT. That same property makes it USELESS
-- on a database that already exists — `CREATE TABLE IF NOT EXISTS` skips a
-- table that is there and silently adds none of its newer columns. An existing
-- database moves forward by applying the migrations it has not had, in order,
-- once each. test/schema.test.mjs rebuilds the schema the live database was
-- created from, applies every migration, and fails if the result differs from
-- this file.
--
-- It used to end in ALTER TABLE statements. Those failed with "duplicate column
-- name" on any second run and stopped the file there, and two of them repeated
-- migration 001, so the file could be run neither fresh-then-migrated nor twice.

CREATE TABLE IF NOT EXISTS licences (
    licence_key          TEXT PRIMARY KEY,
    tier                 TEXT NOT NULL CHECK (tier IN ('managed','byo','trial')),
    status               TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','expired','refunded','suspended')),
    paddle_subscription_id TEXT,
    -- Per-licence fair-use caps. NULL falls back to the Worker's defaults, so
    -- a single licence can be raised without a deploy.
    max_postings_per_day  INTEGER,
    max_refreshes_per_day INTEGER,
    max_saved_queries     INTEGER,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at           TEXT,
    -- Which plan the licence is on. The CAPS are the enforced truth (the
    -- columns above); this records WHICH plan set them, so support can answer
    -- "what am I paying for?" without reverse-engineering it from three cap
    -- numbers, and so a plan whose caps are later revised can be found and
    -- re-applied.
    --
    -- Nullable deliberately: licences issued before plans existed have no
    -- answer, and inventing 'standard' for them would assert something nobody
    -- verified.
    plan                 TEXT,
    -- Set when a Paddle event's price id matched nothing configured, so the
    -- licence took the fallback plan. A row with this set is a customer who may
    -- be on the wrong caps, and it is the only way to find them later — the
    -- webhook logs counts, not payloads, so the event itself is gone.
    plan_unmatched       INTEGER NOT NULL DEFAULT 0
);

-- One licence per Paddle subscription. Two events for one purchase once each
-- found no licence and each minted one; this makes the database refuse the
-- second. NULLs are distinct, so code-minted licences are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS licences_by_subscription
    ON licences (paddle_subscription_id);

-- Managed inference was metered in tokens here until the inference proxy was
-- removed on 2026-09-06. Databases created before then still carry
-- usage_daily.input_tokens, usage_daily.output_tokens and
-- licences.max_tokens_per_day; nothing reads or writes them, and a fresh
-- install does not create them.

CREATE TABLE IF NOT EXISTS usage_daily (
    licence_key TEXT NOT NULL REFERENCES licences(licence_key) ON DELETE CASCADE,
    day         TEXT NOT NULL,
    refreshes   INTEGER NOT NULL DEFAULT 0,
    -- Postings RETURNED, which is the billable unit: 1 credit = 1 job.
    -- Instrumented from day one so the dataset crossover (~1M records/month)
    -- is a measurement, not a guess.
    --
    -- CORRECTED 2026-09-08: this said "roughly 330 daily-active users", which
    -- came from dividing 1M by the build handoff's ASSUMED 3,000 credits per
    -- user per month. Measured consumption is ~15,400 (a comprehensive daily
    -- delta is ~513 postings), so the crossover is nearer **65 daily-active
    -- users** — five times sooner. Believing 330 would keep the product on
    -- per-credit API pricing long past the point where bulk delivery is
    -- cheaper, which is exactly the decision this column exists to inform.
    postings    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (licence_key, day)
);

-- The kill switch and failover order. Changing a row here swaps or disables a
-- provider with no app release — this is the whole point of the abstraction.
CREATE TABLE IF NOT EXISTS providers (
    name     TEXT PRIMARY KEY,
    enabled  INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    note     TEXT
);

INSERT INTO providers (name, enabled, priority, note) VALUES
    ('theirstack', 1, 10, 'Primary. P0 gate: cohort A 23/23, cohort B 18/22.')
ON CONFLICT(name) DO NOTHING;

-- Webhook idempotency. Paddle retries on any non-2xx, and a retry that issues a
-- SECOND licence for one payment is worse than a missed one: the customer holds
-- two keys, the usage meter is split across them, and nothing looks wrong from
-- either side.
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id    TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    action      TEXT NOT NULL,
    received_at TEXT NOT NULL
);

-- The newest thing Paddle has said about each subscription, and when. Paddle
-- does not deliver in order and retries reorder freely, so an older "canceled"
-- could arrive after a newer "resumed" and switch off a paying customer. An
-- event is applied only if it is at least as new as last_event_at, and a
-- licence's status is copied from licence_status, so a grant that arrives
-- after a newer cancellation still honours it.
--
-- paddle_status is Paddle's own word — 'past_due' is recorded here while access
-- continues — and licence_status is what it means for access.
CREATE TABLE IF NOT EXISTS paddle_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    last_event_at   TEXT,
    paddle_status   TEXT,
    licence_status  TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Override codes and the admin console.
--
-- Design carried over from the Wren comp-codes Worker, including its two
-- load-bearing decisions:
--
--  1. There is deliberately NO `uses` counter on `codes`. A counter is a second
--     source of truth that drifts the first time an increment succeeds and the
--     matching insert does not, and it turns "is this code spent?" into a
--     question with two possible answers. Usage is counted from `redemptions`,
--     which is the only place a redemption is recorded, so the two cannot
--     disagree.
--
--  2. The `role` column here — NOT a claim inside the issued licence — decides
--     what the server will do. The licence says what the app should show; the
--     table says what the server permits. Keeping the decision here is what
--     makes withdrawing an administrator take effect immediately, on a machine
--     already holding a perfectly valid licence.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS codes (
    code       TEXT PRIMARY KEY,
    note       TEXT,                          -- who it went to, in plain words
    -- 1 for a normal comp code. The STORE REVIEW code is deliberately NOT
    -- single-use: a reviewer may test on several machines, or re-test after a
    -- rejection, and a spent code turns that into a failed review.
    max_uses   INTEGER NOT NULL DEFAULT 1,
    revoked    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT,                          -- NULL means never
    -- A ladder, not a set: 'admin' presumes 'managed', which presumes 'byo'.
    role       TEXT NOT NULL DEFAULT 'byo'
               CHECK (role IN ('byo', 'managed', 'admin')),
    -- The plan a redeemed licence receives. Without it every redeemed code took
    -- the Worker's full default, so a trial could not be told from a purchase.
    plan       TEXT
);

CREATE TABLE IF NOT EXISTS redemptions (
    code        TEXT NOT NULL,
    licence_key TEXT NOT NULL,
    redeemed_at TEXT NOT NULL,
    PRIMARY KEY (code, licence_key)
);
CREATE INDEX IF NOT EXISTS redemptions_by_code ON redemptions (code);

-- Failed attempts, for rate limiting. Codes carry enough entropy that guessing
-- is not a real threat, but an unbounded endpoint that answers yes or no is
-- still worth a lid.
CREATE TABLE IF NOT EXISTS code_attempts (
    ip       TEXT NOT NULL,
    day      TEXT NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip, day)
);

-- Which licence, if any, came from an override code, and with what role. This
-- is what the admin endpoints re-read on every request.
CREATE TABLE IF NOT EXISTS licence_roles (
    licence_key TEXT PRIMARY KEY,
    role        TEXT NOT NULL,
    from_code   TEXT
);
