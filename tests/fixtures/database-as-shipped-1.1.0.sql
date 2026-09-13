-- The database exactly as Dawnlist 1.1.0 (commit 0f363cd) created it: `db.migrate`
-- on an empty file, then a factsheet, a fit brief and a passed calibration, which
-- 1.1.0 recorded as `calibration_passed_at`. Dumped with sqlite3 iterdump.
-- Regenerate only from that commit, never from the current code.
BEGIN TRANSACTION;
CREATE TABLE assessments (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    bucket      TEXT NOT NULL
                CHECK (bucket IN ('strong','possible','rejected','judgement-call')),
    reason      TEXT NOT NULL,
    -- spec 6.7: a reject on a STATED requirement carries the verbatim line.
    -- An unfetchable requirement is 'not checked', never a fail.
    disqualifying_quote TEXT,
    requirement_checked INTEGER NOT NULL DEFAULT 1,
    full_read   INTEGER NOT NULL DEFAULT 0,
    model       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (job_id, run_id)
);
CREATE TABLE contacts (
    id            INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    email         TEXT,
    -- spec 8.2: on a confirmed bounce the address is cleared so no later run
    -- uses it, and the channel pivots. The bounce does not advance the cadence.
    -- The CHECK is what makes "cleared" real: a bounced contact CANNOT retain
    -- an address, so no later run can read one and try again. Flagging alone
    -- left the dead address sitting there for anything that forgot to look.
    email_bounced INTEGER NOT NULL DEFAULT 0,
    -- spec 9.6: a mutual connection who never replied is not a warm route.
    ever_replied  INTEGER NOT NULL DEFAULT 0,
    do_not_contact INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    -- Makes "cleared" real. A bounced contact CANNOT retain an address, so no
    -- later run can read one and try again. Flagging alone left the dead
    -- address sitting there for anything that forgot to check the flag.
    CHECK (email_bounced = 0 OR email IS NULL)
);
CREATE TABLE decisions (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('pursue','reject','later')),
    note       TEXT,
    decided_at TEXT NOT NULL,
    -- No expiry column on purpose: rejections are permanent (spec 4).
    UNIQUE (job_id)
);
CREATE TABLE documents (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('fit_brief','factsheet')),
    version    INTEGER NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kind, version)
);
INSERT INTO "documents" VALUES(1,'factsheet',1,'Senior Analyst 2019-2024','2026-09-13T13:31:06+00:00');
INSERT INTO "documents" VALUES(2,'fit_brief',1,'brief','2026-09-13T13:31:06+00:00');
CREATE TABLE drafts (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    contact_id     INTEGER REFERENCES contacts(id),
    thread_key     TEXT NOT NULL,
    path           TEXT NOT NULL,
    -- Invariant 2: a draft containing an unresolved [[placeholder]] can never
    -- be exported as send-ready.
    has_placeholder INTEGER NOT NULL DEFAULT 0,
    superseded     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE TABLE jobs (
    id               INTEGER PRIMARY KEY,
    provider         TEXT NOT NULL,
    provider_job_id  TEXT NOT NULL,
    title            TEXT NOT NULL,
    company          TEXT NOT NULL,
    locations_json   TEXT NOT NULL DEFAULT '[]',
    description_text TEXT NOT NULL DEFAULT '',
    posted_at        TEXT,
    salary           TEXT,
    url              TEXT NOT NULL DEFAULT '',
    raw_criteria_json TEXT NOT NULL DEFAULT '{}',
    name_key         TEXT NOT NULL DEFAULT '',
    first_seen_run   INTEGER REFERENCES runs(id),
    -- funnel position + why it stopped there. spec 5.4: never erased.
    funnel_status    TEXT NOT NULL DEFAULT 'swept',
    screen_verdict   TEXT,
    screen_tier      TEXT,
    screen_reason    TEXT,
    -- spec 6.6: dedup is (provider, provider_job_id). Exact, and enforced.
    UNIQUE (provider, provider_job_id)
);
CREATE TABLE kill_families (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    employers_json TEXT NOT NULL,
    kill_json     TEXT NOT NULL,
    -- NOT NULL: SAVES is a required field (spec 5.3 rule 2).
    saves_json    TEXT NOT NULL,
    precedents_json TEXT NOT NULL DEFAULT '[]',
    adopted       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE TABLE near_duplicates (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    other_id   INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    reason     TEXT NOT NULL,
    resolved   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (job_id, other_id)
);
CREATE TABLE opportunities (
    id          INTEGER PRIMARY KEY,
    company     TEXT NOT NULL,
    job_id      INTEGER REFERENCES jobs(id),
    -- spec 8.3: an opportunity is defined by a FIELD it carries, not by its
    -- position in a hierarchy. A "top-level item" test silently mis-classified
    -- opportunities nested two levels down and skipped them in every audit.
    -- `stage` NOT NULL IS that field: carrying a Stage is what makes a record
    -- an opportunity, and `parent_id` is free to be non-null at any depth.
    parent_id   INTEGER REFERENCES opportunities(id),
    -- Stage is ClickUp's 0-based orderindex so an export maps across directly:
    -- 0 Identified, 1 Contacted, 2 In Dialogue, 3 Phone Interview,
    -- 4 In-Person Interview, 5 Offer, 6 Won, 7 Lost, 8 On Hold.
    stage       INTEGER NOT NULL DEFAULT 0 CHECK (stage BETWEEN 0 AND 8),
    -- The visible status is a DERIVED MIRROR of `stage`. When they disagree,
    -- correct the mirror, never the truth. Persisted so drift is detectable.
    status_mirror TEXT,
    -- The only classifier. A record's NAME is never evidence of what it is.
    category    TEXT NOT NULL DEFAULT 'opportunity',
    next_step_on TEXT,
    closed_at   TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE queries (
    id          INTEGER PRIMARY KEY,
    label       TEXT NOT NULL UNIQUE,
    params_json TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    -- handoff 2.1a: delta pulls are mandatory, not an optimisation. This is
    -- the high-water mark passed back as discovered_at_gte on the next run.
    last_discovered_at TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE rule_terms (
    id      INTEGER PRIMARY KEY,
    field   TEXT NOT NULL CHECK (field IN
            ('unsupported_titles','known_employers','strong_terms','contextual_terms')),
    term    TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (field, term)
);
CREATE TABLE run_outputs (
    id       INTEGER PRIMARY KEY,
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path     TEXT NOT NULL UNIQUE,
    kind     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    -- spec 6.4: an incomplete run is reported as incomplete, with the counts
    -- left unread. 'complete' is written only when the likely set is exhausted.
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','complete','incomplete','failed')),
    incomplete_note TEXT,
    -- spec 6.3: never a count without what it excludes. The whole funnel.
    swept           INTEGER NOT NULL DEFAULT 0,
    deduped         INTEGER NOT NULL DEFAULT 0,
    gated           INTEGER NOT NULL DEFAULT 0,
    screened_likely INTEGER NOT NULL DEFAULT 0,
    screened_out    INTEGER NOT NULL DEFAULT 0,
    assessed        INTEGER NOT NULL DEFAULT 0,
    left_unread     INTEGER NOT NULL DEFAULT 0,
    -- spec 6.2: a failed or empty fetch is never "no new jobs".
    fetch_failed    INTEGER NOT NULL DEFAULT 0,
    fetch_error     TEXT
);
CREATE TABLE seen_jobs (
    provider        TEXT NOT NULL,
    provider_job_id TEXT NOT NULL,
    seen_at         TEXT NOT NULL,
    PRIMARY KEY (provider, provider_job_id)
);
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO "settings" VALUES('schema_version','1');
INSERT INTO "settings" VALUES('calibration_passed_at','2026-09-08T09:00:00+00:00');
CREATE TABLE tasks (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open',
    due_on         TEXT,
    -- Retiring a task is not closing the opportunity, and a status alone is
    -- not a record: the evidence is what a later audit actually reads.
    closed_evidence TEXT,
    created_at     TEXT NOT NULL,
    CHECK (status <> 'complete' OR closed_evidence IS NOT NULL)
);
CREATE TABLE touches (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    contact_id     INTEGER REFERENCES contacts(id),
    channel        TEXT NOT NULL CHECK (channel IN ('email','letter','call','other')),
    direction      TEXT NOT NULL CHECK (direction IN ('out','in')),
    occurred_on    TEXT NOT NULL,
    -- An auto-reply is NEVER a genuine reply: it is a scheduling override only.
    is_auto_reply  INTEGER NOT NULL DEFAULT 0,
    ooo_return_on  TEXT,
    bounced        INTEGER NOT NULL DEFAULT 0,
    note           TEXT
);
CREATE INDEX idx_jobs_namekey ON jobs(name_key);
CREATE UNIQUE INDEX idx_one_live_opp_per_company
    ON opportunities(company) WHERE closed_at IS NULL AND stage NOT IN (6, 7);
CREATE UNIQUE INDEX idx_one_open_task_per_opportunity
    ON tasks(opportunity_id) WHERE status IN ('open', 'waiting');
CREATE UNIQUE INDEX idx_one_draft_per_thread
    ON drafts(thread_key, contact_id) WHERE superseded = 0;
COMMIT;
