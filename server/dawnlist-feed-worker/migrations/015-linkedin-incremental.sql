-- LinkedIn Job Library: the pages this Worker spends against LinkedIn's DAILY
-- QUOTA, per licence and in total, and the cursor that lets a search read only
-- what is new.
--
-- WHY. LinkedIn throttles the Job Library per application and member per UTC
-- day (HTTP 429) and there is ONE token for every customer, so one licence
-- running many searches can spend what everybody else needs. `linkedin_usage`
-- is the per-licence allowance and `linkedin_usage_global` the ceiling on the
-- whole Worker, kept under LinkedIn's own limit so it is ours that refuses.
--
-- `linkedin_cursors` holds, per licence, per search and per keyword, the newest
-- posting time a COMPLETE scan reached, so the next scan stops there (less an
-- overlap) instead of re-reading the whole window. It stores a hash of the
-- search and the keyword, never a copy of any posting, and is purged after 30
-- days (retention.js).
CREATE TABLE IF NOT EXISTS linkedin_usage (
    licence_key TEXT NOT NULL REFERENCES licences(licence_key) ON DELETE CASCADE,
    day         TEXT NOT NULL,
    pages       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (licence_key, day)
);

CREATE TABLE IF NOT EXISTS linkedin_usage_global (
    day   TEXT PRIMARY KEY,
    pages INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS linkedin_cursors (
    licence_key TEXT NOT NULL REFERENCES licences(licence_key) ON DELETE CASCADE,
    query_hash  TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    newest_ms   INTEGER NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (licence_key, query_hash, keyword)
);
