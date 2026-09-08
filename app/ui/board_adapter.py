"""Board rows from the database, and repairs back to it."""
from __future__ import annotations

import sqlite3
from datetime import date

from app.core.board_repo import (add_task, audit_board, load_board,
                                 next_steps,
                                 record_outbound, repair_mirror, set_stage)
from app.core.cadence import Channel
from app.core.tracker import Stage, open_children
from app.ui.board import COLUMN_SETTING, BoardRow


def board_rows(conn: sqlite3.Connection, *,
               today: date | None = None) -> tuple[list[BoardRow], dict[str, list]]:
    opps = load_board(conn)
    findings = audit_board(conn)
    steps = next_steps(conn, opps, today=today)

    parity = {d.opportunity_id: str(d) for d in findings["parity_defects"]}
    bounces = {d.opportunity_id: str(d) for d in findings["bounce_corrections"]}

    rows: list[BoardRow] = []
    for opp in opps:
        step = steps.get(opp.id)
        live = open_children(opp)
        rows.append(BoardRow(
            opportunity_id=opp.id,
            company=opp.company,
            stage=opp.stage,
            status=opp.status,
            next_step_on=getattr(step, "due_on", None),
            next_step_channels=", ".join(
                c.value for c in getattr(step, "channels", ())),
            open_task=live[0].title if live else "",
            parity_defect=parity.get(opp.id, ""),
            bounce_defect=bounces.get(opp.id, ""),
            job_title=opp.job_title,
            location=opp.location,
            salary=opp.salary,
            job_url=opp.job_url,
            posted_at=opp.posted_at,
            created_at=opp.created_at,
            last_outbound_on=opp.last_outbound_on,
        ))
    return rows, findings


def load_visible_columns(conn: sqlite3.Connection) -> list[str] | None:
    """The user's saved column set, or None to use the defaults.

    None and an empty list are different answers: None means "never chosen",
    empty would mean "chose nothing", and only the first should silently take
    the defaults.
    """
    row = conn.execute("SELECT value FROM settings WHERE key=?",
                       (COLUMN_SETTING,)).fetchone()
    if row is None or not row[0]:
        return None
    return [k for k in row[0].split(",") if k]


def save_visible_columns(conn: sqlite3.Connection, keys: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (COLUMN_SETTING, keys))
    conn.commit()


def connect_board(window, conn: sqlite3.Connection) -> None:
    """Wire the board's repair buttons.

    The two repairs are deliberately separate actions: correcting the MIRROR is
    routine, while correcting the STAGE after a bounce is an admission that the
    pipeline read was wrong. Collapsing them into one "fix it" button would let
    a bounce be papered over as a status tidy-up.
    """
    window.repair_requested.connect(lambda oid: repair_mirror(conn, oid))
    window.bounce_repair_requested.connect(
        lambda oid: set_stage(conn, oid, Stage.IDENTIFIED))
    window.sent_recorded.connect(lambda oid: record_sent(conn, oid))
    window.task_added.connect(lambda oid, title: add_task(conn, oid, title))
    # Restore the saved column set BEFORE wiring the save, or applying it
    # would immediately write back what was just read.
    saved = load_visible_columns(conn)
    if saved is not None:
        window.set_visible_columns(saved)
    window.columns_changed.connect(lambda keys: save_visible_columns(conn, keys))


def record_sent(conn: sqlite3.Connection, opportunity_id: str,
                *, today: date | None = None) -> None:
    """The user confirming a drafted message actually went out.

    Dawnlist never sends, so this is the only evidence that exists. Until it is
    recorded the cadence sits at rung zero and redrafts the same first contact
    every week, scheduling no follow-up at all.

    The touch is attributed to the contact the live draft was addressed to,
    rather than to whichever contact happens to be first: a touch recorded
    against the wrong person schedules the chase against someone who was never
    written to. If no draft is on file the touch is still recorded against the
    opportunity — a message sent by hand is still a message sent.

    Moving IDENTIFIED to CONTACTED is a consequence, not a second action. A
    stage that lags the evidence is the drift the parity audit then reports.
    """
    row = conn.execute(
        "SELECT contact_id FROM drafts WHERE opportunity_id=? AND superseded=0 "
        "ORDER BY id DESC LIMIT 1", (int(opportunity_id),)).fetchone()

    record_outbound(conn, opportunity_id, Channel.EMAIL,
                    today or date.today(),
                    contact_id=row["contact_id"] if row else None)

    current = conn.execute("SELECT stage FROM opportunities WHERE id=?",
                           (int(opportunity_id),)).fetchone()
    if current and Stage(current["stage"]) is Stage.IDENTIFIED:
        set_stage(conn, opportunity_id, Stage.CONTACTED)
