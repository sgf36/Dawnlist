"""Board persistence: stage and mirror move together, evidence drives cadence."""
from datetime import date

import pytest

from app.core import db
from app.core.board_repo import (add_task, audit_board,
                                 commit_determination, create_opportunity,
                                 determination_writes, load_board,
                                 load_opportunity, load_touches, next_steps,
                                 record_bounce, record_outbound, repair_mirror,
                                 retire_task, set_stage)
from app.core.cadence import Channel
from app.core.tracker import JobCategory, Stage, TrackerError, Write

TUE = date(2026, 9, 8)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def contact(conn, opp_id, email="jo@example.com"):
    return conn.execute(
        "INSERT INTO contacts(opportunity_id, name, email, created_at)"
        " VALUES(?, 'Jo', ?, 'x')", (opp_id, email)).lastrowid


# -- load -------------------------------------------------------------------
def test_the_board_loads_every_stage_in_one_pass(conn):
    """Auditing status-slice by status-slice leaves a different hole each
    time, and every hole looks like 'nothing due'."""
    for stage in (Stage.IDENTIFIED, Stage.CONTACTED, Stage.WON, Stage.LOST,
                  Stage.ON_HOLD):
        create_opportunity(conn, f"Co {stage.value}", stage=stage)
    board = load_board(conn)
    assert len(board) == 5
    assert {o.stage for o in board} == {Stage.IDENTIFIED, Stage.CONTACTED,
                                        Stage.WON, Stage.LOST, Stage.ON_HOLD}


def test_a_new_opportunity_gets_its_mirror_derived(conn):
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    opp = load_board(conn)[0]
    assert opp.status == "waiting"
    assert opp.expected_status == "waiting"


def test_tasks_load_against_their_opportunity(conn):
    oid = create_opportunity(conn, "Acme")
    add_task(conn, str(oid), "Send the letter", TUE)
    opp = load_board(conn)[0]
    assert len(opp.tasks) == 1 and opp.tasks[0].is_open


def test_a_bounce_on_a_contact_reaches_the_opportunity(conn):
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    cid = contact(conn, oid)
    record_bounce(conn, str(oid), cid, TUE)
    assert load_board(conn)[0].email_bounced


# -- stage and mirror move together -----------------------------------------
def test_setting_a_stage_moves_the_mirror_too(conn):
    oid = create_opportunity(conn, "Acme")
    set_stage(conn, str(oid), Stage.PHONE_INTERVIEW)
    assert load_board(conn)[0].status == "phone interview"


def test_a_terminal_stage_closes_the_opportunity(conn):
    oid = create_opportunity(conn, "Acme")
    set_stage(conn, str(oid), Stage.LOST)
    assert load_board(conn)[0].closed_at is not None
    # ...and the employer slot is freed for a fresh approach.
    create_opportunity(conn, "Acme")


def test_on_hold_does_not_close_the_opportunity(conn):
    oid = create_opportunity(conn, "Acme")
    set_stage(conn, str(oid), Stage.ON_HOLD)
    assert load_board(conn)[0].closed_at is None, "paused is not dead"


def test_repair_corrects_the_mirror_never_the_stage(conn):
    oid = create_opportunity(conn, "Acme", stage=Stage.OFFER)
    conn.execute("UPDATE opportunities SET status_mirror='open'")
    conn.commit()
    assert audit_board(conn)["parity_defects"]

    repair_mirror(conn, str(oid))
    board = load_board(conn)
    assert board[0].status == "received offer"
    assert board[0].stage is Stage.OFFER, "the Stage must be untouched"
    assert not audit_board(conn)["parity_defects"]


# -- advancing --------------------------------------------------------------
def test_an_outbound_advances_identified_to_contacted_only(conn):
    oid = create_opportunity(conn, "Acme")
    record_outbound(conn, str(oid), Channel.EMAIL, TUE)
    assert load_board(conn)[0].stage is Stage.CONTACTED

    record_outbound(conn, str(oid), Channel.EMAIL, date(2026, 9, 15))
    assert load_board(conn)[0].stage is Stage.CONTACTED, (
        "a send never implies a reply")


def test_a_bounce_walks_the_stage_back_and_clears_the_address(conn):
    oid = create_opportunity(conn, "Acme")
    cid = contact(conn, oid)
    record_outbound(conn, str(oid), Channel.EMAIL, TUE, contact_id=cid)
    assert load_board(conn)[0].stage is Stage.CONTACTED

    record_bounce(conn, str(oid), cid, TUE)
    assert load_board(conn)[0].stage is Stage.IDENTIFIED, (
        "a hard bounce means never contacted, not unanswered")
    row = conn.execute("SELECT email FROM contacts WHERE id=?", (cid,)).fetchone()
    assert row["email"] is None


# -- cadence ----------------------------------------------------------------
def test_next_step_is_computed_from_evidenced_touches(conn):
    oid = create_opportunity(conn, "Acme")
    record_outbound(conn, str(oid), Channel.EMAIL, TUE)
    steps = next_steps(conn, load_board(conn), today=TUE)
    assert steps[str(oid)].due_on == date(2026, 9, 15)


def test_a_bounced_touch_does_not_start_the_five_day_clock(conn):
    oid = create_opportunity(conn, "Acme")
    cid = contact(conn, oid)
    record_bounce(conn, str(oid), cid, TUE)
    step = next_steps(conn, load_board(conn), today=date(2026, 9, 9))[str(oid)]
    assert step.due_on == date(2026, 9, 9), "the pivot is due now"
    assert Channel.EMAIL not in step.channels


def test_on_hold_and_terminal_stages_get_no_cadence(conn):
    held = create_opportunity(conn, "Held", stage=Stage.ON_HOLD)
    lost = create_opportunity(conn, "Lost", stage=Stage.LOST)
    steps = next_steps(conn, load_board(conn), today=TUE)
    assert str(held) not in steps and str(lost) not in steps


def test_mutual_poc_records_get_no_cadence(conn):
    oid = create_opportunity(conn, "Intro", category=JobCategory.MUTUAL_POC,
                             stage=Stage.CONTACTED)
    assert str(oid) not in next_steps(conn, load_board(conn), today=TUE)


# -- tasks ------------------------------------------------------------------
def test_only_one_open_task_per_opportunity(conn):
    import sqlite3 as sq
    oid = create_opportunity(conn, "Acme")
    add_task(conn, str(oid), "Chase")
    with pytest.raises(sq.IntegrityError):
        add_task(conn, str(oid), "Chase again")


def test_retiring_a_task_requires_evidence_and_frees_the_slot(conn):
    oid = create_opportunity(conn, "Acme")
    tid = add_task(conn, str(oid), "Chase")
    with pytest.raises(TrackerError, match="needs evidence"):
        retire_task(conn, str(tid), "  ")
    retire_task(conn, str(tid), "Letter posted 2026-09-01, tracking ABC")
    add_task(conn, str(oid), "Next step")
    assert load_board(conn)[0].tasks[-1].is_open


def test_retiring_a_task_does_not_close_the_opportunity(conn):
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    tid = add_task(conn, str(oid), "Chase")
    retire_task(conn, str(tid), "replied by phone 3 Sept")
    opp = load_board(conn)[0]
    assert opp.closed_at is None and opp.stage is Stage.CONTACTED


# -- an employer's answer reaches the board (spec 8.3) ----------------------
#
# `apply_determination` encoded the whole rule and nothing called it, so the
# ladder had no way past Contacted: `record_outbound` raises Identified to
# Contacted and stops, because a send never implies a reply. Every pursuit sat
# at Contacted for ever while the cadence went on chasing it.
def test_a_reply_advances_the_stage_and_its_mirror(conn):
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    writes = determination_writes(conn, str(oid), positive=True,
                                  advance_to=Stage.IN_DIALOGUE)
    commit_determination(conn, str(oid), writes)

    opp = load_opportunity(conn, str(oid))
    assert opp.stage is Stage.IN_DIALOGUE
    assert opp.status == "waiting", "the mirror has to move with the stage"


def test_computing_the_writes_changes_nothing(conn):
    """A determination is pre-filled for confirmation, never committed by
    the act of asking what it would do."""
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    add_task(conn, str(oid), "Chase")

    determination_writes(conn, str(oid), positive=False)

    opp = load_opportunity(conn, str(oid))
    assert opp.stage is Stage.CONTACTED
    assert [t.status for t in opp.tasks] == ["open"]


def test_a_negative_answer_closes_the_whole_tree(conn):
    """The parent goes Lost and every subtask is rendered `no offer`. The
    cascade is legitimate only because the parent closes at the same time."""
    oid = create_opportunity(conn, "Acme", stage=Stage.PHONE_INTERVIEW)
    add_task(conn, str(oid), "Prepare for the call")

    writes = determination_writes(conn, str(oid), positive=False)
    commit_determination(conn, str(oid), writes)

    opp = load_opportunity(conn, str(oid))
    assert opp.stage is Stage.LOST
    assert opp.status == "no offer"
    assert [t.status for t in opp.tasks] == ["no offer"]
    assert opp.tasks[0].is_open is False


def test_a_task_belonging_to_someone_else_is_not_touched(conn):
    """Task ids and opportunity ids are both INTEGER PRIMARY KEY and collide
    from row one, so routing a write by its id alone writes to the wrong
    record."""
    other = create_opportunity(conn, "Beta", stage=Stage.CONTACTED)
    other_task = add_task(conn, str(other), "Their chase")
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)

    writes = determination_writes(conn, str(oid), positive=False)
    writes.append(Write("task", str(other_task), "status", "no offer"))
    commit_determination(conn, str(oid), writes)

    assert load_opportunity(conn, str(other)).tasks[0].status == "open"


def test_no_offer_on_a_child_of_a_live_parent_is_still_refused(conn):
    """The guard is re-read from the database rather than assumed from the
    order of the list — a reordered list must not slip a forbidden state past
    it."""
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    task_id = add_task(conn, str(oid), "Chase")
    with pytest.raises(TrackerError):
        commit_determination(
            conn, str(oid),
            [Write("task", str(task_id), "status", "no offer")])


def test_a_positive_determination_cannot_close_an_opportunity(conn):
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    with pytest.raises(TrackerError):
        determination_writes(conn, str(oid), positive=True,
                             advance_to=Stage.LOST)
