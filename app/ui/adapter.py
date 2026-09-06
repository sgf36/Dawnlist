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
