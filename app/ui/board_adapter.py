"""Board rows from the database, and repairs back to it."""
from __future__ import annotations

import sqlite3
from datetime import date

from app.core.board_repo import (audit_board, load_board, next_steps,
                                 repair_mirror, set_stage)
from app.core.tracker import Stage, open_children
from app.ui.board import BoardRow


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
        ))
    return rows, findings


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
