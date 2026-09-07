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
from datetime import datetime, timezone

from app.core.pipeline import RunOutcome
from app.core.screen import Verdict
from app.ui.review import ReviewRow

#: The bucket the UI uses for a posting the deterministic screen removed. It is
#: not a model verdict, and must never be shown as one.
SCREENED_OUT = "screened-out"

VALID_DECISIONS = {"pursue", "reject", "later"}


def rows_from_outcome(outcome: RunOutcome) -> list[ReviewRow]:
    """Every posting the run touched, assessed or not."""
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


def counts_for_ui(outcome: RunOutcome) -> dict[str, int]:
    return outcome.funnel()


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
    """The most recent run that actually swept anything.

    Deliberately not MAX(id): a run row is opened by any run that produces
    outputs, an outreach run included, and those sweep no jobs. Taking the
    newest row regardless would empty the review window every time the user
    drafted their outreach — the shortlist would vanish for no visible reason.
    """
    row = conn.execute(
        "SELECT MAX(id) AS id FROM runs WHERE swept > 0").fetchone()
    if row and row["id"] is not None:
        return row["id"]
    # No run has swept yet: fall back to the newest, so a first run that
    # fetched nothing still shows its (empty) result rather than nothing at all.
    row = conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()
    return row["id"] if row and row["id"] is not None else None


def rows_from_db(conn: sqlite3.Connection, run_id: int | None = None,
                 *, include_decided: bool = False) -> list[ReviewRow]:
    """Rebuild the review rows from what a run persisted.

    `rows_from_outcome` only works while the run is still in memory, which
    means it can only ever serve the process that did the run. Opening the app
    the next morning is the normal case, and without this the window opens
    empty however much work the last run did.

    Decided postings are dropped by default: a pile that does not shrink as it
    is worked is the reason people stop working it.
    """
    run_id = run_id if run_id is not None else latest_run_id(conn)
    if run_id is None:
        return []

    sql = """
        SELECT j.provider, j.provider_job_id, j.title, j.company,
               j.locations_json, j.description_text, j.url,
               j.screen_verdict, j.screen_reason,
               a.bucket, a.reason, a.disqualifying_quote, a.requirement_checked
          FROM jobs j
          LEFT JOIN assessments a ON a.job_id = j.id AND a.run_id = ?
         WHERE j.first_seen_run = ?
    """
    if not include_decided:
        sql += " AND j.id NOT IN (SELECT job_id FROM decisions)"
    sql += " ORDER BY j.id"

    near = near_duplicate_notes(conn)
    rows: list[ReviewRow] = []
    for r in conn.execute(sql, (run_id, run_id)):
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

        rows.append(ReviewRow(
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
        ))
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
