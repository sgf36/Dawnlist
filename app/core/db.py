"""SQLite data layer (handoff Part 6).

Location: the platform app-data dir via platformdirs. NEVER a cloud-synced
folder — the OneDrive lock-file lesson generalises, and a sync conflict copy of
a half-written SQLite file is a corrupted database with no error message.

Two structural choices are worth reading before changing anything:

  * `run_outputs.run_id` is NOT NULL and foreign-keys to `runs`. That is
    invariant 13 / spec 6.5 as a constraint rather than a procedure. A real
    failure produced a complete 101-row assessment including a strong match,
    renamed the file to avoid a clash, never registered the new name, and
    nothing would ever have read it. Prose is what failed; a constraint cannot
    be forgotten.

  * `decisions` rows with kind='reject' are PERMANENT and have no expiry
    column, deliberately. `seen_jobs` rolls off after ~45 days so a recurring
    alert cannot re-list forever; rejections never do.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SEEN_RETENTION_DAYS = 45

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
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

-- Invariant 13. An output cannot exist without the run that produced it.
CREATE TABLE IF NOT EXISTS run_outputs (
    id       INTEGER PRIMARY KEY,
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    path     TEXT NOT NULL UNIQUE,
    kind     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queries (
    id          INTEGER PRIMARY KEY,
    label       TEXT NOT NULL UNIQUE,
    params_json TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    -- handoff 2.1a: delta pulls are mandatory, not an optimisation. This is
    -- the high-water mark passed back as discovered_at_gte on the next run.
    last_discovered_at TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
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
CREATE INDEX IF NOT EXISTS idx_jobs_namekey ON jobs(name_key);

-- spec 6.6: near-duplicates are FLAGGED as judgement calls, never silently
-- dropped and never silently re-surfaced.
CREATE TABLE IF NOT EXISTS near_duplicates (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    other_id   INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    reason     TEXT NOT NULL,
    resolved   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (job_id, other_id)
);

CREATE TABLE IF NOT EXISTS assessments (
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

CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('pursue','reject','later')),
    note       TEXT,
    decided_at TEXT NOT NULL,
    -- No expiry column on purpose: rejections are permanent (spec 4).
    UNIQUE (job_id)
);

-- Rolling ~45 days so a recurring alert cannot re-list forever and the table
-- cannot grow unbounded. Distinct from `decisions`, which never expires.
CREATE TABLE IF NOT EXISTS seen_jobs (
    provider        TEXT NOT NULL,
    provider_job_id TEXT NOT NULL,
    seen_at         TEXT NOT NULL,
    PRIMARY KEY (provider, provider_job_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
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
-- spec 9.5: one live opportunity per employer. Won (6) and Lost (7) free it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_live_opp_per_company
    ON opportunities(company) WHERE closed_at IS NULL AND stage NOT IN (6, 7);

-- Action tasks — the children. They never carry a Stage; their status is
-- evidence-based, not a mirror of the parent's.
CREATE TABLE IF NOT EXISTS tasks (
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
-- At most one non-terminal action task per opportunity, so the same chase
-- cannot go out twice. Dedup tests open OR waiting.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_task_per_opportunity
    ON tasks(opportunity_id) WHERE status IN ('open', 'waiting');

CREATE TABLE IF NOT EXISTS contacts (
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

-- The evidence log. spec 8.1: the cadence interval is computed from ACTUAL
-- evidenced touches, never from a cached "last contact" field.
CREATE TABLE IF NOT EXISTS touches (
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

CREATE TABLE IF NOT EXISTS drafts (
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
-- spec 9.3: one draft per recipient per thread, ever. Revise in place.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_draft_per_thread
    ON drafts(thread_key, contact_id) WHERE superseded = 0;

CREATE TABLE IF NOT EXISTS kill_families (
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

CREATE TABLE IF NOT EXISTS rule_terms (
    id      INTEGER PRIMARY KEY,
    field   TEXT NOT NULL CHECK (field IN
            ('unsupported_titles','known_employers','strong_terms','contextual_terms')),
    term    TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (field, term)
);

-- Versioned rows, never updated in place: every override is a sentence the
-- brief was missing, and the history is what makes the calibration loop real.
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('fit_brief','factsheet')),
    version    INTEGER NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kind, version)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_db_path(app_name: str = "Dawnlist") -> Path:
    """Per-user app-data dir. Never a synced folder."""
    try:
        from platformdirs import user_data_dir
        base = Path(user_data_dir(app_name, "Spencer Fields Software"))
    except ImportError:  # keeps the engine importable before deps are installed
        base = Path.home() / ".dawnlist"
    base.mkdir(parents=True, exist_ok=True)
    return base / "dawnlist.sqlite3"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else default_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO settings(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),))
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Runs — the funnel, and the registration constraint.
# ---------------------------------------------------------------------------

class RunIncomplete(RuntimeError):
    """Raised when a run is finished without the likely set being exhausted."""


@contextmanager
def run(conn: sqlite3.Connection) -> Iterator["Run"]:
    """Open a run. On any exception the run is recorded as failed, never lost.

    spec 6.2/6.4: a crash, a context exhaustion or an empty fetch is named as
    such. The one thing that must never happen is a bad run filed as a normal
    one.
    """
    cur = conn.execute("INSERT INTO runs(started_at) VALUES(?)", (_now(),))
    conn.commit()
    r = Run(conn, cur.lastrowid)
    try:
        yield r
    except Exception as exc:  # noqa: BLE001
        conn.execute(
            "UPDATE runs SET status='failed', finished_at=?, incomplete_note=? "
            "WHERE id=?", (_now(), f"{type(exc).__name__}: {exc}"[:500], r.id))
        conn.commit()
        raise
    else:
        if r._finished is None:
            r.finish()


class Run:
    def __init__(self, conn: sqlite3.Connection, run_id: int):
        self.conn = conn
        self.id = run_id
        self._finished: str | None = None

    def record_counts(self, **counts: int) -> None:
        allowed = {"swept", "deduped", "gated", "screened_likely", "screened_out",
                   "assessed", "left_unread"}
        bad = set(counts) - allowed
        if bad:
            raise ValueError(f"unknown funnel counters: {sorted(bad)}")
        sets = ", ".join(f"{k}=?" for k in counts)
        self.conn.execute(f"UPDATE runs SET {sets} WHERE id=?",
                          (*counts.values(), self.id))
        self.conn.commit()

    def record_fetch_failure(self, error: str) -> None:
        """spec 6.2. Absence of evidence is not evidence of absence."""
        self.conn.execute(
            "UPDATE runs SET fetch_failed=1, fetch_error=? WHERE id=?",
            (error[:500], self.id))
        self.conn.commit()

    def register_output(self, path: str | Path, kind: str = "review") -> None:
        """Invariant 13. The FK makes an unregistered output impossible."""
        self.conn.execute(
            "INSERT INTO run_outputs(run_id, path, kind, created_at) "
            "VALUES(?,?,?,?) ON CONFLICT(path) DO NOTHING",
            (self.id, str(path), kind, _now()))
        self.conn.commit()

    def finish(self, *, left_unread: int = 0, note: str | None = None) -> None:
        status = "complete" if left_unread == 0 else "incomplete"
        self._finished = _now()
        self.conn.execute(
            "UPDATE runs SET status=?, finished_at=?, left_unread=?, "
            "incomplete_note=? WHERE id=?",
            (status, self._finished, left_unread, note, self.id))
        self.conn.commit()

    def funnel(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (self.id,)).fetchone()
        return dict(row)


def orphan_outputs(conn: sqlite3.Connection, output_dir: Path) -> list[Path]:
    """Files on disk that no run registered (spec 6.5).

    Tested against `run_outputs` — every output ever produced — and NOT against
    an "awaiting decision" list, which is pruned as decisions are made and so
    would flag every healthy completed file.
    """
    known = {r["path"] for r in conn.execute("SELECT path FROM run_outputs")}
    if not output_dir.exists():
        return []
    return sorted(p for p in output_dir.iterdir()
                  if p.is_file() and str(p) not in known)


def prune_seen(conn: sqlite3.Connection, days: int = SEEN_RETENTION_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    cur = conn.execute("DELETE FROM seen_jobs WHERE seen_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def record_bounce(conn: sqlite3.Connection, contact_id: int) -> None:
    """Mark a contact bounced AND clear the dead address, atomically.

    spec 8.2. Doing these as two statements is how one of them gets skipped:
    the flag lands, the address stays, and a later run reads it and sends to a
    mailbox that does not exist. The schema CHECK refuses that state, so this
    is the only way to get there.
    """
    conn.execute(
        "UPDATE contacts SET email = NULL, email_bounced = 1 WHERE id = ?",
        (contact_id,))
    conn.commit()
