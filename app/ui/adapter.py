"""Joining the run to the screen, and the user's click back to the database.

The pipeline produces a `RunOutcome`; the review window consumes `ReviewRow`s.
Keeping the translation here means the window knows nothing about the feed and
the pipeline knows nothing about Qt, so either can be replaced on its own.

Two rules travel with the rows rather than being re-derived in the UI:

  * a screened-out posting still becomes a row. spec 5.4 — the `unlikely` pile
    is browsable, and an over-aggressive rule must show up as rows the user
    disagrees with rather than as months of silence;
  * a downgraded verdict carries its downgrade reason, so the user sees that
    the app overrode the model and why.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.dedup import recover_stranded
from app.core.pipeline import RunOutcome
from app.core.screen import Verdict
from app.ui.review import ReviewRow

#: The bucket the UI uses for a posting the deterministic screen removed. It is
#: not a model verdict, and must never be shown as one.
SCREENED_OUT = "screened-out"

VALID_DECISIONS = {"pursue", "reject", "later"}


def rows_from_outcome(outcome: RunOutcome) -> list[ReviewRow]:
    """Every posting the run touched, assessed or not — from memory.

    The twin of `rows_from_db`, which rebuilds the same rows from what
    `persist` wrote. Two builders is a drift risk and the drift would be
    invisible: a field added to one shows the user different things depending
    on whether they are looking at the run that just happened or the one they
    opened this morning. `test_the_two_row_builders_agree` pins them together.
    """
    verdicts = {v.job.provider_job_id: v
                for v in (outcome.assessment.verdicts if outcome.assessment else [])}
    # Flagged, never merged. Both sides of a pair carry the note, so whichever
    # one the user opens tells them the other exists.
    near: dict[str, str] = {}
    for nd in (outcome.deduped.near_duplicates if outcome.deduped else []):
        near[nd.job.provider_job_id] = f"{nd.other.title} — {nd.other.company}"
        near[nd.other.provider_job_id] = f"{nd.job.title} — {nd.job.company}"
    rows: list[ReviewRow] = []

    for result in (outcome.screen.results if outcome.screen else []):
        job = result.job
        verdict = verdicts.get(job.provider_job_id)

        if verdict is not None:
            bucket = verdict.bucket
            reason = verdict.reason
            quote = verdict.disqualifying_quote
            checked = verdict.requirement_checked
            downgrade = verdict.downgrade_reason
        elif result.verdict is Verdict.LIKELY:
            # Survived the screen but was never judged — an unread posting is
            # an unknown, never a rejection (spec 6.7, invariant 12).
            bucket = "judgement-call"
            reason = "not assessed in this run"
            quote, checked, downgrade = None, False, None
        else:
            bucket = SCREENED_OUT
            reason = ""
            quote, checked, downgrade = None, True, None

        rows.append(ReviewRow(
            job_id=f"{job.provider}:{job.provider_job_id}",
            title=job.title,
            company=job.company,
            location=", ".join(job.locations),
            url=job.url,
            description=job.description_text,
            bucket=bucket,
            reason=reason,
            disqualifying_quote=quote,
            requirement_checked=checked,
            downgrade_reason=downgrade,
            screen_reason=result.reason if not result.is_likely else "",
            contained=result.contained,
            near_duplicate=near.get(job.provider_job_id, ""),
        ))
    return rows


def incomplete_note(outcome: RunOutcome) -> str:
    """What to show in the funnel bar's warning. Empty when the run was clean.

    A fetch failure outranks an assessment gap in the message, because it means
    the swept number itself is understated — the user is looking at a partial
    morning, not a complete one with a few rows left over.
    """
    if outcome.fetch_errors:
        return outcome.fetch_errors[0]
    if outcome.assessment and outcome.assessment.errors:
        return outcome.assessment.errors[0]
    return ""


def split_job_id(job_id: str) -> tuple[str, str]:
    provider, _, provider_job_id = job_id.partition(":")
    return provider, provider_job_id


def record_decision(conn: sqlite3.Connection, job_id: str, decision: str) -> None:
    """Persist one Pursue / Reject / Later.

    A rejection is PERMANENT and never expires (spec 4), so this writes to
    `decisions`, which has no expiry column, rather than to the rolling
    `seen_jobs` table.

    The upsert lets a user change their mind — a decision is theirs to revise —
    but nothing here decides anything on their behalf.
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(f"unknown decision {decision!r}")

    provider, provider_job_id = split_job_id(job_id)
    row = conn.execute(
        "SELECT id FROM jobs WHERE provider=? AND provider_job_id=?",
        (provider, provider_job_id)).fetchone()
    if row is None:
        raise LookupError(f"no stored job for {job_id!r}; persist the run first")

    conn.execute(
        """INSERT INTO decisions(job_id, kind, decided_at) VALUES(?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET
               kind = excluded.kind,
               decided_at = excluded.decided_at""",
        (row["id"], decision,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()

    if decision == "pursue":
        open_opportunity(conn, row["id"])


def open_opportunity(conn: sqlite3.Connection, job_row_id: int) -> int | None:
    """Put a pursued posting on the board.

    Without this, Pursue wrote a `decisions` row and stopped. Nothing appeared
    on the board, so nothing ever came due, so no draft was ever written — the
    chain was severed between deciding and doing, and every module either side
    of the break was correct and tested.

    Returns the opportunity id, or None when the employer already has a live
    one. Spec 9.5 allows one live opportunity per employer, so a second posting
    at the same company joins the existing pursuit rather than opening a rival
    to it — two live records for one employer is how the same person gets
    written to twice in a week.
    """
    from app.core.board_repo import create_opportunity

    job = conn.execute("SELECT company FROM jobs WHERE id=?",
                       (job_row_id,)).fetchone()
    if job is None:
        return None

    live = conn.execute(
        "SELECT id FROM opportunities WHERE company=? AND closed_at IS NULL "
        "AND stage NOT IN (6, 7)", (job["company"],)).fetchone()
    if live is not None:
        return None

    return create_opportunity(conn, job["company"], job_id=job_row_id)


def rejected_keys(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Every permanently rejected posting, for the next run's gate.

    This is what makes a rejection compound: each one is a posting never
    assessed again, and the saving grows run on run.
    """
    return {
        (r["provider"], r["provider_job_id"])
        for r in conn.execute(
            "SELECT j.provider, j.provider_job_id FROM decisions d "
            "JOIN jobs j ON j.id = d.job_id WHERE d.kind = 'reject'")
    }


def connect_window(window, conn: sqlite3.Connection) -> None:
    """Wire the window's decisions straight through to the database."""
    window.decided.connect(lambda job_id, decision:
                           record_decision(conn, job_id, decision))


def latest_run_id(conn: sqlite3.Connection) -> int | None:
    """The most recent daily search, whatever it swept — or failing that, the
    most recent run that swept anything.

    Deliberately not MAX(id): a run row is opened by any run that produces
    outputs, an outreach run included, and those sweep no jobs. Taking the
    newest row regardless would empty the review window every time the user
    drafted their outreach — the shortlist would vanish for no visible reason.

    Nor `swept > 0` alone any longer (PIPELINE-P6). A search whose fetch failed
    sweeps nothing, so that rule skipped straight past it to yesterday's run,
    and the window showed an older shortlist as though it were today's with no
    sign that this morning had gone wrong. A daily search is now recognised by
    its `kind`, and chosen however little it found, so its status is what the
    window reports. Undecided postings from before it still arrive, marked as
    carried forward. `swept > 0` remains for job-alert imports and for rows
    written before `kind` existed.
    """
    from app.core.schedule import SWEEP

    row = conn.execute(
        "SELECT MAX(id) AS id FROM runs WHERE kind = ? OR swept > 0",
        (SWEEP,)).fetchone()
    if row and row["id"] is not None:
        return row["id"]
    # No run has swept yet: fall back to the newest, so a first run that
    # fetched nothing still shows its (empty) result rather than nothing at all.
    row = conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()
    return row["id"] if row and row["id"] is not None else None


@dataclass(frozen=True)
class RunStatus:
    """What the window says about a run, read back from its row."""
    run_id: int
    status: str
    started_at: str
    incomplete_note: str = ""
    fetch_error: str = ""

    @property
    def went_wrong(self) -> bool:
        # 'incomplete' covers both a partial fetch and postings left unread;
        # either way the shortlist is not the whole of what was there.
        return self.status in ("failed", "incomplete")


def run_status(conn: sqlite3.Connection, run_id: int | None) -> RunStatus | None:
    if run_id is None:
        return None
    row = conn.execute(
        "SELECT id, status, started_at, incomplete_note, fetch_error "
        "FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        return None
    return RunStatus(run_id=row["id"], status=row["status"],
                     started_at=row["started_at"],
                     incomplete_note=row["incomplete_note"] or "",
                     fetch_error=row["fetch_error"] or "")


#: The columns both row queries read. Written once because the two of them
#: have to agree: a column added to one and not the other shows the user a
#: different row depending on which run it came from.
_ROW_COLUMNS = """
        SELECT j.provider, j.provider_job_id, j.title, j.company,
               j.locations_json, j.description_text, j.url,
               j.screen_verdict, j.screen_reason,
               a.bucket, a.reason, a.disqualifying_quote, a.requirement_checked
"""


def _row_from_sql(r, near: dict[str, str], *,
                  carried_forward: bool = False) -> ReviewRow:
    """One database row as the review screen sees it."""
    if r["bucket"]:
        bucket = r["bucket"]
        reason = r["reason"] or ""
        quote = r["disqualifying_quote"]
        checked = bool(r["requirement_checked"])
    elif r["screen_verdict"] == Verdict.LIKELY.value:
        # Survived the screen, never judged. Same rule as
        # `rows_from_outcome`: an unread posting is an unknown, not a
        # rejection.
        bucket, reason = "judgement-call", "not assessed in this run"
        quote, checked = None, False
    else:
        bucket, reason = SCREENED_OUT, ""
        quote, checked = None, True

    if carried_forward:
        # Said on the row itself, not only in a flag the window may or may not
        # draw: the user's first question about a posting they do not remember
        # seeing today is where it came from.
        reason = ("carried forward from an earlier run"
                  + (f" — {reason}" if reason else ""))

    return ReviewRow(
        job_id=f"{r['provider']}:{r['provider_job_id']}",
        title=r["title"],
        company=r["company"],
        location=", ".join(json.loads(r["locations_json"] or "[]")),
        url=r["url"],
        description=r["description_text"],
        bucket=bucket,
        reason=reason,
        disqualifying_quote=quote,
        requirement_checked=checked,
        downgrade_reason=None,
        screen_reason=r["screen_reason"] or "",
        contained=False,
        near_duplicate=near.get(r["provider_job_id"], ""),
        carried_forward=carried_forward,
    )


def decided_job_keys(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """`(provider, provider_job_id)` for everything the user has ruled on.

    By provider job id, never by name key — see `recover_stranded`. Two runs
    normalised the same employer differently and three already-decided
    postings came back as new.
    """
    return {(r["provider"], str(r["provider_job_id"]))
            for r in conn.execute(
                "SELECT j.provider, j.provider_job_id "
                "  FROM decisions d JOIN jobs j ON j.id = d.job_id")}


def stranded_rows(conn: sqlite3.Connection, run_id: int,
                  near: dict[str, str]) -> list[ReviewRow]:
    """Undecided postings from EARLIER runs (spec 6.5).

    The board reads one run. That is right for the funnel counts and wrong for
    the pile: a strong match surfaced yesterday and left undecided vanishes the
    moment tonight's run finishes, and from the user's chair the app has lost
    it. `orphan_outputs` has always reported the same failure from the file
    side; nothing has ever recovered anything.

    The two rules stay in `recover_stranded` rather than moving into this SQL
    on purpose. Filtering rejections and decided ids in the query would make
    the recovery look right while the rules that make it right went unrun —
    and those are the rules that were learned the expensive way.
    """
    sql = _ROW_COLUMNS + """
          FROM jobs j
          LEFT JOIN assessments a
                 ON a.job_id = j.id
                AND a.run_id = (SELECT MAX(run_id) FROM assessments
                                 WHERE job_id = j.id)
         WHERE j.first_seen_run IS NOT NULL
           AND j.first_seen_run <> ?
           AND (a.bucket IS NOT NULL OR j.screen_verdict = ?)
         ORDER BY j.id
    """
    candidates = [dict(r) for r in
                  conn.execute(sql, (run_id, Verdict.LIKELY.value))]
    recovered = recover_stranded(candidates, decided_job_keys(conn))
    return [_row_from_sql(r, near, carried_forward=True) for r in recovered]


def rows_from_db(conn: sqlite3.Connection, run_id: int | None = None,
                 *, include_decided: bool = False,
                 recover: bool = True) -> list[ReviewRow]:
    """Rebuild the review rows from what a run persisted.

    `rows_from_outcome` only works while the run is still in memory, which
    means it can only ever serve the process that did the run. Opening the app
    the next morning is the normal case, and without this the window opens
    empty however much work the last run did.

    Decided postings are dropped by default: a pile that does not shrink as it
    is worked is the reason people stop working it.

    `recover` appends what earlier runs left undecided. Off when a caller wants
    one run exactly — a funnel count, or the test that pins the two row
    builders together.
    """
    run_id = run_id if run_id is not None else latest_run_id(conn)
    if run_id is None:
        return []

    sql = _ROW_COLUMNS + """
          FROM jobs j
          LEFT JOIN assessments a ON a.job_id = j.id AND a.run_id = ?
         WHERE j.first_seen_run = ?
    """
    if not include_decided:
        sql += " AND j.id NOT IN (SELECT job_id FROM decisions)"
    sql += " ORDER BY j.id"

    near = near_duplicate_notes(conn)
    rows = [_row_from_sql(r, near) for r in conn.execute(sql, (run_id, run_id))]
    if recover and not include_decided:
        rows.extend(stranded_rows(conn, run_id, near))
    return rows


def near_duplicate_notes(conn: sqlite3.Connection) -> dict[str, str]:
    """provider_job_id -> "the other posting", for every flagged pair.

    Both sides carry the note, so whichever one the user opens tells them the
    other exists. One row per pair is stored; the symmetry is built here.
    """
    notes: dict[str, str] = {}
    for r in conn.execute(
            "SELECT a.provider_job_id AS a_id, a.title AS a_title, "
            "       a.company AS a_company, "
            "       b.provider_job_id AS b_id, b.title AS b_title, "
            "       b.company AS b_company "
            "  FROM near_duplicates n "
            "  JOIN jobs a ON a.id = n.job_id "
            "  JOIN jobs b ON b.id = n.other_id "
            " WHERE n.resolved = 0"):
        notes[r["a_id"]] = f"{r['b_title']} — {r['b_company']}"
        notes[r["b_id"]] = f"{r['a_title']} — {r['a_company']}"
    return notes
