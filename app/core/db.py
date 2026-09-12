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

#: How long a posting nobody decided on keeps its description text.
#:
#: Screened out: a run only reads postings posted within 45 days, so one first
#: seen that long ago is past any window it would be read in again.
#: Screened in but undecided: longer, because the review pile carries those
#: forward and someone working through a backlog still needs the text to
#: decide.
SCREENED_OUT_DESCRIPTION_DAYS = 45
UNDECIDED_DESCRIPTION_DAYS = 90

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


#: Numbered schema changes, applied in order to any database older than them.
#:
#: `SCHEMA` is version 1 and stays exactly as it shipped. It is all
#: `CREATE ... IF NOT EXISTS`, which builds a fresh database but can never add
#: a column to a table someone already has — so before this there was no way
#: to change the shape of an installed database at all, and `schema_version`
#: was written on every start and read by nothing.
#:
#: Append only. A step that has shipped is never edited: the installs that ran
#: it will not run it again.
MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (2, (
        # When a description was cleared, so an empty one reads as removed on
        # purpose rather than as a posting that never had one.
        "ALTER TABLE jobs ADD COLUMN description_pruned_at TEXT",
    )),
    (3, (
        # One row per model request, so what a run cost on the user's key —
        # and whether the cached prefix ever cached — can be read back.
        """CREATE TABLE model_calls (
               id                 INTEGER PRIMARY KEY,
               run_id             INTEGER NOT NULL
                                  REFERENCES runs(id) ON DELETE CASCADE,
               model              TEXT NOT NULL,
               stop_reason        TEXT,
               full_read          INTEGER NOT NULL DEFAULT 0,
               input_tokens       INTEGER NOT NULL DEFAULT 0,
               output_tokens      INTEGER NOT NULL DEFAULT 0,
               cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
               cache_write_tokens INTEGER NOT NULL DEFAULT 0,
               created_at         TEXT NOT NULL
           )""",
    )),
    (4, (
        # What a run was for. The calibration sample is now stored as a run of
        # its own, and without this it could not be told from a morning sweep.
        "ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'sweep'",
    )),
)

SCHEMA_VERSION = max(number for number, _ in MIGRATIONS)


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()
    try:
        return int(row[0]) if row else 1
    except (TypeError, ValueError):
        return 1


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION, one numbered step at a time.

    A step and its version number commit together or not at all: one that
    fails part-way leaves neither a half-altered table nor a version claiming
    it finished, so the next start retries it cleanly. A database already
    NEWER than this build is left as it is; the one-line version this replaced
    overwrote it with an older number.
    """
    conn.executescript(SCHEMA)
    current = schema_version(conn)
    for number, statements in MIGRATIONS:
        if number <= current:
            continue
        conn.execute("BEGIN")
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(number),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        current = number


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Runs — the funnel, and the registration constraint.
# ---------------------------------------------------------------------------

class RunIncomplete(RuntimeError):
    """Raised when a run is finished without the likely set being exhausted."""


#: What a `runs` row was for, written when the row is OPENED rather than when
#: the run ends: the schedule asks "has a search started today?" while a run is
#: still going, and a row tagged afterwards is untagged for exactly as long as
#: the run takes.
#:
#: Every kind is named here because only SWEEP may stand in for the day's
#: scheduled search. Drafting outreach at 06:50 must not cancel the 07:00 run,
#: and it did: `outreach/run.py` called `db.run(conn)` and took the default.
SWEEP = "sweep"
OUTREACH = "outreach"
ALERTS = "alerts"
CALIBRATION = "calibration"


@contextmanager
def run(conn: sqlite3.Connection, kind: str = SWEEP) -> Iterator["Run"]:
    """Open a run. On any exception the run is recorded as failed, never lost.

    spec 6.2/6.4: a crash, a context exhaustion or an empty fetch is named as
    such. The one thing that must never happen is a bad run filed as a normal
    one.
    """
    cur = conn.execute("INSERT INTO runs(started_at, kind) VALUES(?, ?)",
                       (_now(), kind))
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


def advance_query_marks(conn: sqlite3.Connection, marks: dict) -> None:
    """Move each named query's delta mark to its own fetch time.

    By label, never across the table: a query that failed or did not run this
    time keeps the mark for the window it has not read.
    """
    for label, when in marks.items():
        conn.execute("UPDATE queries SET last_discovered_at=? WHERE label=?",
                     (when.isoformat(timespec="seconds"), label))
    conn.commit()


def prune_seen(conn: sqlite3.Connection, days: int = SEEN_RETENTION_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    cur = conn.execute("DELETE FROM seen_jobs WHERE seen_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def prune_descriptions(conn: sqlite3.Connection, *,
                       screened_out_days: int = SCREENED_OUT_DESCRIPTION_DAYS,
                       undecided_days: int = UNDECIDED_DESCRIPTION_DAYS) -> int:
    """Clear the description of old postings nobody decided on. Rows stay.

    A description averages about 7,400 characters and a morning sweep stores
    hundreds, and every one was kept for the life of the install — while only
    the text of a posting the user decided on, or put on the board, is ever
    read again. The row, its title, its screen verdict and its assessment all
    stay (spec 5.4: never erased). A pasted posting has no run and is kept:
    the user chose it by hand.
    """
    now = datetime.now(timezone.utc)

    def before(days: int) -> str:
        return (now - timedelta(days=days)).isoformat(timespec="seconds")

    cur = conn.execute(
        """UPDATE jobs SET description_text = '', description_pruned_at = ?
            WHERE description_text <> ''
              AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.job_id = jobs.id)
              AND NOT EXISTS (SELECT 1 FROM opportunities o
                               WHERE o.job_id = jobs.id)
              AND EXISTS (SELECT 1 FROM runs r
                           WHERE r.id = jobs.first_seen_run
                             AND r.started_at < CASE
                                 WHEN jobs.screen_verdict = 'unlikely' THEN ?
                                 ELSE ? END)""",
        (now.isoformat(timespec="seconds"), before(screened_out_days),
         before(undecided_days)))
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
