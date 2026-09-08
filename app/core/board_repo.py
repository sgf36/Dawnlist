"""Loading and saving the board.

`tracker.py` stays pure logic with no database dependency — it is the part that
encodes the rules, and it must be testable without a schema. This module is the
only place that knows both.

The load is deliberately ONE pass over every stage, including the closed ones.
Auditing status-slice by status-slice leaves a different hole each time, and
every hole looks like "nothing due" (spec 8.3).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from app.core.cadence import Channel, Direction, Touch, next_step
from app.core.tracker import (STATUS_MIRROR, JobCategory, Opportunity, Stage,
                              Task, TrackerError, advance_for_outbound, audit,
                              validate_child_status)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def _first_location(locations_json) -> str:
    """The first location from the posting, for a single narrow column.

    Postings routinely carry three overlapping strings for one place
    ("London", "London, UK", "London, England, United Kingdom"). The first is
    the shortest useful one, and joining all three makes the column unreadable
    without adding information.
    """
    if not locations_json:
        return ""
    try:
        values = json.loads(locations_json)
    except (TypeError, ValueError):
        return ""
    return str(values[0]) if isinstance(values, list) and values else ""


def load_board(conn: sqlite3.Connection) -> list[Opportunity]:
    """Every opportunity, at every stage, with its tasks and bounce state.

    Paginated to exhaustion by construction — a single query, no slicing.
    """
    opps: dict[int, Opportunity] = {}
    # LEFT JOIN, not JOIN. An opportunity may have no posting behind it — one
    # created by hand, or one whose job row was never linked — and an inner
    # join would drop it from the board entirely. A tracker that silently
    # omits rows is worse than one with empty cells.
    for row in conn.execute("""
            SELECT o.*,
                   j.title    AS job_title,
                   j.url      AS job_url,
                   j.salary   AS job_salary,
                   j.locations_json AS job_locations,
                   j.posted_at AS job_posted_at
              FROM opportunities o
              LEFT JOIN jobs j ON j.id = o.job_id
             ORDER BY o.id"""):
        opps[row["id"]] = Opportunity(
            id=str(row["id"]),
            company=row["company"],
            stage=Stage(row["stage"]),
            status=row["status_mirror"] or "",
            parent_id=str(row["parent_id"]) if row["parent_id"] else None,
            category=row["category"],
            closed_at=_as_date(row["closed_at"]),
            job_title=row["job_title"] or "",
            job_url=row["job_url"] or "",
            salary=row["job_salary"] or "",
            location=_first_location(row["job_locations"]),
            posted_at=_as_date(row["job_posted_at"]),
            created_at=_as_date(row["created_at"]),
        )

    # The newest OUTBOUND touch per opportunity. Outbound only: an incoming
    # reply is not evidence that you have contacted anyone, and the cadence is
    # computed from what actually went out.
    for row in conn.execute("""
            SELECT opportunity_id, MAX(occurred_on) AS last_out
              FROM touches
             WHERE direction = 'out'
             GROUP BY opportunity_id"""):
        opp = opps.get(row["opportunity_id"])
        if opp is not None:
            opp.last_outbound_on = _as_date(row["last_out"])

    for row in conn.execute("SELECT * FROM tasks ORDER BY id"):
        opp = opps.get(row["opportunity_id"])
        if opp is None:
            continue
        opp.tasks.append(Task(
            id=str(row["id"]),
            title=row["title"],
            status=row["status"],
            parent_id=str(row["opportunity_id"]),
            due_on=_as_date(row["due_on"]),
            closed_evidence=row["closed_evidence"],
        ))

    # A bounce is a property of a contact, but it outranks the opportunity's
    # Stage, so it has to be loaded with the board rather than looked up later.
    for row in conn.execute(
            "SELECT opportunity_id FROM contacts WHERE email_bounced = 1"):
        opp = opps.get(row["opportunity_id"])
        if opp is not None:
            opp.email_bounced = True

    return list(opps.values())


def load_touches(conn: sqlite3.Connection, opportunity_id: str) -> list[Touch]:
    rows = conn.execute(
        "SELECT * FROM touches WHERE opportunity_id = ? ORDER BY occurred_on",
        (int(opportunity_id),)).fetchall()
    out: list[Touch] = []
    for r in rows:
        occurred = _as_date(r["occurred_on"])
        if occurred is None:
            continue
        out.append(Touch(
            channel=Channel(r["channel"]),
            direction=Direction(r["direction"]),
            occurred_on=occurred,
            is_auto_reply=bool(r["is_auto_reply"]),
            ooo_return_on=_as_date(r["ooo_return_on"]),
            bounced=bool(r["bounced"]),
        ))
    return out


def next_steps(conn: sqlite3.Connection, opps: list[Opportunity],
               *, today: date | None = None) -> dict[str, object]:
    """The next due touch per opportunity, computed from evidenced touches.

    Only opportunities carrying an active cadence are computed. On Hold is
    skipped here but is NOT dead — it still gets a reply check elsewhere.
    """
    out: dict[str, object] = {}
    for opp in opps:
        if not opp.stage.is_live or opp.category == JobCategory.MUTUAL_POC:
            continue
        out[opp.id] = next_step(load_touches(conn, opp.id), today=today)
    return out


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def create_opportunity(conn: sqlite3.Connection, company: str, *,
                       job_id: int | None = None,
                       stage: Stage = Stage.IDENTIFIED,
                       category: str = JobCategory.OPPORTUNITY,
                       parent_id: int | None = None) -> int:
    """Open an opportunity. The status mirror is derived, never passed in."""
    cur = conn.execute(
        """INSERT INTO opportunities(company, job_id, parent_id, stage,
               status_mirror, category, created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (company, job_id, parent_id, int(stage), STATUS_MIRROR[stage],
         category, _now()))
    conn.commit()
    return cur.lastrowid


def set_stage(conn: sqlite3.Connection, opportunity_id: str,
              stage: Stage) -> None:
    """Move a stage AND its mirror together.

    They are written in one statement on purpose: doing it in two is how the
    mirror drifts, and the drift is invisible until an audit finds it.
    """
    closed = _now() if stage.is_terminal else None
    conn.execute(
        "UPDATE opportunities SET stage=?, status_mirror=?, closed_at=? WHERE id=?",
        (int(stage), STATUS_MIRROR[stage], closed, int(opportunity_id)))
    conn.commit()


def repair_mirror(conn: sqlite3.Connection, opportunity_id: str) -> None:
    """Correct the mirror to match the Stage. Never the other way round."""
    row = conn.execute("SELECT stage FROM opportunities WHERE id=?",
                       (int(opportunity_id),)).fetchone()
    if row is None:
        return
    conn.execute("UPDATE opportunities SET status_mirror=? WHERE id=?",
                 (STATUS_MIRROR[Stage(row["stage"])], int(opportunity_id)))
    conn.commit()


def record_outbound(conn: sqlite3.Connection, opportunity_id: str,
                    channel: Channel, on: date, *,
                    contact_id: int | None = None) -> None:
    """Log a touch and advance the stage by the one step a send justifies.

    Identified -> Contacted, and nothing further: In Dialogue and beyond need
    an inbound reply, and inferring them from a send corrupts the pipeline.
    """
    conn.execute(
        """INSERT INTO touches(opportunity_id, contact_id, channel, direction,
               occurred_on) VALUES(?,?,?,'out',?)""",
        (int(opportunity_id), contact_id, channel.value, on.isoformat()))
    row = conn.execute("SELECT stage FROM opportunities WHERE id=?",
                       (int(opportunity_id),)).fetchone()
    if row is not None:
        current = Stage(row["stage"])
        advanced = advance_for_outbound(current)
        if advanced is not current:
            set_stage(conn, opportunity_id, advanced)
    conn.commit()


def record_bounce(conn: sqlite3.Connection, opportunity_id: str,
                  contact_id: int, on: date) -> None:
    """A bounce clears the address, logs the failed touch, and walks the Stage
    BACK to Identified — a hard bounce means never contacted, not unanswered.
    """
    from app.core.db import record_bounce as clear_address

    clear_address(conn, contact_id)
    conn.execute(
        """INSERT INTO touches(opportunity_id, contact_id, channel, direction,
               occurred_on, bounced) VALUES(?,?,'email','out',?,1)""",
        (int(opportunity_id), contact_id, on.isoformat()))
    row = conn.execute("SELECT stage FROM opportunities WHERE id=?",
                       (int(opportunity_id),)).fetchone()
    if row is not None and Stage(row["stage"]).is_live:
        set_stage(conn, opportunity_id, Stage.IDENTIFIED)
    conn.commit()


def add_task(conn: sqlite3.Connection, opportunity_id: str, title: str,
             due_on: date | None = None) -> int:
    """Create the next action task. At most one may be open per opportunity —
    the schema enforces it, so a duplicate chase cannot be created."""
    row = conn.execute("SELECT stage FROM opportunities WHERE id=?",
                       (int(opportunity_id),)).fetchone()
    if row is None:
        raise LookupError(f"no opportunity {opportunity_id!r}")
    validate_child_status("open", Stage(row["stage"]))
    cur = conn.execute(
        """INSERT INTO tasks(opportunity_id, title, status, due_on, created_at)
           VALUES(?,?,'open',?,?)""",
        (int(opportunity_id), title,
         due_on.isoformat() if due_on else None, _now()))
    conn.commit()
    return cur.lastrowid


def retire_task(conn: sqlite3.Connection, task_id: str, evidence: str) -> None:
    """Retiring a task is not closing the opportunity, and a status alone is
    not a record — the evidence is what a later audit actually reads."""
    if not (evidence or "").strip():
        raise TrackerError(
            "a retirement needs evidence; the status alone is not a record")
    conn.execute(
        "UPDATE tasks SET status='complete', closed_evidence=? WHERE id=?",
        (evidence, int(task_id)))
    conn.commit()


def audit_board(conn: sqlite3.Connection) -> dict[str, list]:
    """One pass over every stage. Returns findings, applies nothing."""
    return audit(load_board(conn))
