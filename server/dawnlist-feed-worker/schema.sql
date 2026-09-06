-- Dawnlist feed Worker — D1 schema.
--
-- Metering only. This database holds counts, licences and provider flags; it
-- never holds job descriptions, CVs, queries with personal text, or anything
-- else that would make the store privacy labels a lie.

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
    expires_at           TEXT
);

CREATE TABLE IF NOT EXISTS usage_daily (
    licence_key TEXT NOT NULL REFERENCES licences(licence_key) ON DELETE CASCADE,
    day         TEXT NOT NULL,
    refreshes   INTEGER NOT NULL DEFAULT 0,
    -- Postings RETURNED, which is the billable unit: 1 credit = 1 job.
    -- Instrumented from day one so the dataset crossover (~1M records/month,
    -- roughly 330 daily-active users) is a measurement, not a guess.
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
