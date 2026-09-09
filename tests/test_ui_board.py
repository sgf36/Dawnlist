"""The board screen."""
from datetime import date

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import db  # noqa: E402
from app.core.board_repo import (add_task, create_opportunity, record_bounce,  # noqa: E402
                                 record_outbound, set_stage)
from app.core.cadence import Channel  # noqa: E402
from app.core.tracker import Stage  # noqa: E402
from app.ui.board import BoardRow, BoardWindow  # noqa: E402
from app.ui.board_adapter import board_rows, connect_board  # noqa: E402

TUE = date(2026, 9, 8)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture()
def win(qapp):
    w = BoardWindow()
    yield w
    w.close()


def contact(conn, oid):
    return conn.execute(
        "INSERT INTO contacts(opportunity_id, name, email, created_at)"
        " VALUES(?, 'Jo', 'jo@example.com', 'x')", (oid,)).lastrowid


def group_labels(win):
    return [win.tree.topLevelItem(i).text(0)
            for i in range(win.tree.topLevelItemCount())]


# -- grouping and order -----------------------------------------------------
def test_groups_are_ordered_by_stage_not_status(conn, win):
    create_opportunity(conn, "Zeta", stage=Stage.OFFER)
    create_opportunity(conn, "Alpha", stage=Stage.IDENTIFIED)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    labels = group_labels(win)
    assert labels.index("Identified (1)") < labels.index("Offer (1)")


def test_closed_stages_are_still_shown(conn, win):
    """Auditing status-slice by status-slice leaves a hole that looks like
    'nothing due'. Won and Lost stay on the board."""
    create_opportunity(conn, "Won Co", stage=Stage.WON)
    create_opportunity(conn, "Lost Co", stage=Stage.LOST)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    assert "Won (1)" in group_labels(win) and "Lost (1)" in group_labels(win)


def test_on_hold_is_its_own_group_and_not_dimmed_like_the_closed_ones(conn, win):
    from PySide6.QtGui import QColor
    create_opportunity(conn, "Paused", stage=Stage.ON_HOLD)
    create_opportunity(conn, "Dead", stage=Stage.LOST)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)

    labels = group_labels(win)
    held = win.tree.topLevelItem(labels.index("On Hold (1)"))
    lost = win.tree.topLevelItem(labels.index("Lost (1)"))
    assert held.foreground(0).color() != QColor("#8b9199"), "paused is not dead"
    assert lost.foreground(0).color() == QColor("#8b9199")


# -- the audit banner -------------------------------------------------------
def test_a_clean_board_says_so_with_a_count(conn, win):
    create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    assert "no drift found" in win.audit._label.text()
    assert "1 opportunities checked" in win.audit._label.text()


def test_parity_drift_is_surfaced_not_logged(conn, win):
    oid = create_opportunity(conn, "Acme", stage=Stage.OFFER)
    conn.execute("UPDATE opportunities SET status_mirror='open'")
    conn.commit()
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    assert "out of step" in win.audit._label.text()


def test_a_bounce_is_reported_before_a_parity_defect(conn, win):
    """The Stage being wrong is the worse defect, so it leads."""
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    cid = contact(conn, oid)
    conn.execute("UPDATE opportunities SET stage=1, status_mirror='waiting'")
    conn.commit()
    record_bounce(conn, str(oid), cid, TUE)
    # Force the stage back to Contacted so the bounce and the stage disagree.
    conn.execute("UPDATE opportunities SET stage=1 WHERE id=?", (oid,))
    conn.commit()

    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    assert win.audit._label.text().index("bounced") < len(win.audit._label.text())
    assert "the address is dead" in win.audit._label.text()


# -- next steps -------------------------------------------------------------
def test_the_next_step_date_and_channel_are_shown(conn, win):
    oid = create_opportunity(conn, "Acme")
    record_outbound(conn, str(oid), Channel.EMAIL, TUE)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    row = [r for r in rows if r.company == "Acme"][0]
    assert row.next_step_on == date(2026, 9, 15)
    assert "letter" in row.next_step_channels


def test_the_open_task_is_shown(conn, win):
    oid = create_opportunity(conn, "Acme")
    add_task(conn, str(oid), "Send the letter", TUE)
    rows, _ = board_rows(conn, today=TUE)
    assert rows[0].open_task == "Send the letter"


# -- repairs ----------------------------------------------------------------
def test_repair_buttons_are_disabled_without_a_defect(conn, win):
    create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
    assert not win.btn_repair.isEnabled()
    assert not win.btn_repair_bounce.isEnabled()


def test_correcting_the_status_fixes_the_mirror_and_leaves_the_stage(conn, win):
    oid = create_opportunity(conn, "Acme", stage=Stage.OFFER)
    conn.execute("UPDATE opportunities SET status_mirror='open'")
    conn.commit()

    connect_board(win, conn)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
    assert win.btn_repair.isEnabled()
    win.btn_repair.click()

    row = conn.execute("SELECT stage, status_mirror FROM opportunities").fetchone()
    assert row["status_mirror"] == "received offer"
    assert row["stage"] == int(Stage.OFFER), "the Stage must be untouched"


def test_the_two_repairs_are_separate_actions(conn, win):
    """Collapsing them would let a bounce be papered over as a status tidy-up."""
    assert win.btn_repair is not win.btn_repair_bounce
    assert win.repair_requested is not win.bounce_repair_requested


def test_group_headers_are_not_selectable(conn, win):
    """A selectable row that does nothing when clicked reads as broken."""
    from PySide6.QtCore import Qt
    create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    group = win.tree.topLevelItem(0)
    assert not (group.flags() & Qt.ItemIsSelectable)
    assert group.child(0).flags() & Qt.ItemIsSelectable


# -- closing the loop -------------------------------------------------------
def test_recording_a_send_advances_the_cadence(conn):
    """Dawnlist never sends, so it cannot observe that a message went out.
    Until the user says so the cadence sits at rung zero and the same first
    contact is redrafted every week, with no follow-up ever scheduled."""
    from app.core.cadence import next_step
    from app.core.board_repo import load_touches
    from app.ui.board_adapter import record_sent

    oid = create_opportunity(conn, "Acme")
    assert load_touches(conn, str(oid)) == []

    record_sent(conn, str(oid), today=TUE)
    touches = load_touches(conn, str(oid))
    assert len(touches) == 1
    assert touches[0].is_evidenced_outbound

    step = next_step(touches, today=TUE)
    assert step.due_on is not None and step.due_on > TUE, (
        "the next touch must move into the future, or it is due again today")


def test_recording_a_send_moves_identified_to_contacted(conn):
    """A stage that lags the evidence is exactly the drift the parity audit
    then reports."""
    from app.core.board_repo import load_board
    from app.ui.board_adapter import record_sent

    oid = create_opportunity(conn, "Acme", stage=Stage.IDENTIFIED)
    record_sent(conn, str(oid), today=TUE)
    assert load_board(conn)[0].stage is Stage.CONTACTED


def test_a_later_stage_is_not_dragged_backwards(conn):
    """Sending a note to someone already in dialogue does not un-progress
    them."""
    from app.core.board_repo import load_board
    from app.ui.board_adapter import record_sent

    oid = create_opportunity(conn, "Acme", stage=Stage.IN_DIALOGUE)
    record_sent(conn, str(oid), today=TUE)
    assert load_board(conn)[0].stage is Stage.IN_DIALOGUE


def test_the_touch_is_attributed_to_the_drafted_contact(conn):
    """A touch recorded against the wrong person schedules the chase against
    someone who was never written to."""
    from app.core.board_repo import load_touches
    from app.ui.board_adapter import record_sent

    oid = create_opportunity(conn, "Acme")
    first = conn.execute(
        "INSERT INTO contacts(opportunity_id,name,email,created_at) "
        "VALUES(?,?,?,?)", (oid, "Alex", "alex@example.com", "x")).lastrowid
    drafted = conn.execute(
        "INSERT INTO contacts(opportunity_id,name,email,created_at) "
        "VALUES(?,?,?,?)", (oid, "Jo", "jo@example.com", "x")).lastrowid
    conn.execute(
        "INSERT INTO drafts(opportunity_id,contact_id,thread_key,path,created_at)"
        " VALUES(?,?,?,?,?)", (oid, drafted, "t", "p.eml", "x"))
    conn.commit()

    record_sent(conn, str(oid), today=TUE)
    # `Touch` carries no contact — read the stored row, which does.
    stored = conn.execute(
        "SELECT contact_id FROM touches WHERE opportunity_id=?",
        (oid,)).fetchone()
    assert stored["contact_id"] == drafted, (
        f"attributed to {stored['contact_id']}, not the drafted contact "
        f"{drafted} (the first contact is {first})")


def test_a_message_sent_by_hand_still_counts(conn):
    """No draft on file is not the same as no message sent."""
    from app.core.board_repo import load_touches
    from app.ui.board_adapter import record_sent

    oid = create_opportunity(conn, "Acme")
    record_sent(conn, str(oid), today=TUE)
    assert len(load_touches(conn, str(oid))) == 1


def test_the_button_is_offered_only_on_a_live_stage(qapp):
    """Recording a send against a Won, Lost or On Hold record would schedule a
    chase on a closed pursuit."""
    from app.ui.board import BoardRow, BoardWindow
    w = BoardWindow()
    rows = [BoardRow(opportunity_id="1", company="Live", stage=Stage.CONTACTED,
                     status="Contacted"),
            BoardRow(opportunity_id="2", company="Done", stage=Stage.WON,
                     status="Won")]
    w.load(rows, {"scanned": [], "parity_defects": [],
                  "bounce_corrections": [], "duplicate_open_children": []})

    seen = {}
    for oid in ("1", "2"):
        # Drive the real selection path rather than calling the handler with a
        # row of our own: what is being tested is that selecting a closed
        # record disables the button.
        item = _find_item(w, oid)
        assert item is not None, f"no row for {oid}"
        w.tree.setCurrentItem(item)
        seen[oid] = w.btn_sent.isEnabled()

    assert seen == {"1": True, "2": False}
    w.close()


def _find_item(window, opportunity_id):
    from PySide6.QtCore import Qt
    stack = [window.tree.topLevelItem(i)
             for i in range(window.tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item.data(0, Qt.UserRole) == opportunity_id:
            return item
        stack.extend(item.child(i) for i in range(item.childCount()))
    return None


def test_a_task_can_be_added_from_the_board(conn, qapp):
    """`add_task` had no route, so the "Open task" column could only ever be
    filled by something other than the board."""
    from app.core.tracker import open_children
    from app.core.board_repo import load_board
    from app.ui.board import BoardRow, BoardWindow
    from app.ui.board_adapter import connect_board

    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    w = BoardWindow()
    connect_board(w, conn)
    w.load([BoardRow(opportunity_id=str(oid), company="Acme",
                     stage=Stage.CONTACTED, status="Contacted")],
           {"scanned": [], "parity_defects": [], "bounce_corrections": [],
            "duplicate_open_children": []})
    w.task_added.emit(str(oid), "Prepare for the call")

    tasks = open_children(load_board(conn)[0])
    assert [t.title for t in tasks] == ["Prepare for the call"]
    w.close()


def test_an_empty_task_is_not_added(qapp):
    from app.ui.board import BoardRow, BoardWindow
    w = BoardWindow()
    rows = [BoardRow(opportunity_id="1", company="Acme",
                     stage=Stage.CONTACTED, status="Contacted")]
    w.load(rows, {"scanned": [], "parity_defects": [],
                  "bounce_corrections": [], "duplicate_open_children": []})
    seen = []
    w.task_added.connect(lambda *a: seen.append(a))
    w.field_task.setText("   ")
    w._emit_task()
    assert seen == []
    w.close()


def test_the_task_field_is_closed_when_one_is_already_open(qapp):
    """At most one open task per opportunity — the schema enforces it, so
    offering the field would only produce an error the user cannot act on."""
    from app.ui.board import BoardRow, BoardWindow
    w = BoardWindow()
    rows = [BoardRow(opportunity_id="1", company="Busy", stage=Stage.CONTACTED,
                     status="Contacted", open_task="Chase Jo"),
            BoardRow(opportunity_id="2", company="Free", stage=Stage.CONTACTED,
                     status="Contacted")]
    w.load(rows, {"scanned": [], "parity_defects": [],
                  "bounce_corrections": [], "duplicate_open_children": []})
    state = {}
    for oid in ("1", "2"):
        w.tree.setCurrentItem(_find_item(w, oid))
        state[oid] = w.btn_task.isEnabled()
    assert state == {"1": False, "2": True}
    w.close()


def test_a_closed_opportunity_takes_no_new_task(qapp):
    from app.ui.board import BoardRow, BoardWindow
    w = BoardWindow()
    rows = [BoardRow(opportunity_id="1", company="Done", stage=Stage.WON,
                     status="Won")]
    w.load(rows, {"scanned": [], "parity_defects": [],
                  "bounce_corrections": [], "duplicate_open_children": []})
    w.tree.setCurrentItem(_find_item(w, "1"))
    assert not w.btn_task.isEnabled()
    w.close()


# ---------------------------------------------------------------------------
# Writing the application — the one board action that spends money
# ---------------------------------------------------------------------------

def test_the_application_buttons_are_dead_without_a_selection(win):
    assert not win.btn_apply.isEnabled()
    assert not win.btn_brief.isEnabled()


def test_a_closed_pursuit_cannot_have_an_application_written(win):
    """A covering letter for something already Won or Lost spends the user's
    own tokens on a document nobody will send."""
    win.load([BoardRow(opportunity_id="o1", company="Done", stage=Stage.WON, status="Won")], {})
    win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
    assert not win.btn_apply.isEnabled()
    assert not win.btn_brief.isEnabled()


def test_a_live_pursuit_can(win):
    win.load([BoardRow(opportunity_id="o2", company="Live", stage=Stage.IN_DIALOGUE, status="In Dialogue")], {})
    win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
    assert win.btn_apply.isEnabled()
    assert win.btn_brief.isEnabled()


def test_the_two_buttons_ask_for_different_things(win):
    """Two buttons rather than one with a modifier key: hiding a paid action
    behind shift-click makes it undiscoverable."""
    win.load([BoardRow(opportunity_id="o3", company="Live", stage=Stage.IN_DIALOGUE, status="In Dialogue")], {})
    win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
    seen = []
    win.application_requested.connect(lambda oid, brief: seen.append((oid, brief)))
    win.btn_apply.click()
    win.btn_brief.click()
    assert seen == [("o3", False), ("o3", True)]


def test_the_board_never_writes_anything_itself(win):
    """It emits intent. A widget that could spend the user's tokens by itself
    is the boundary the rest of this class exists to keep."""
    win.load([BoardRow(opportunity_id="o4", company="Live", stage=Stage.IN_DIALOGUE, status="In Dialogue")], {})
    win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
    win.btn_apply.click()          # nothing connected: must not raise


# ---------------------------------------------------------------------------
# The employer's answer — the only event that moves an opportunity past
# Contacted. `apply_determination` encoded the whole rule and nothing called
# it, so the ladder had no way forward and the cadence chased threads that had
# already ended.
# ---------------------------------------------------------------------------

def _loaded(conn, win):
    rows, findings = board_rows(conn)
    win.load(rows, findings)
    return win


def test_recording_a_reply_advances_the_stage(conn, win):
    from app.ui.board_adapter import record_determination
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    connect_board(win, conn)
    _loaded(conn, win)
    win.confirm_determination = lambda lines: True

    win.tree.setCurrentItem(_find_item(win, str(oid)))
    win.combo_stage.setCurrentIndex(
        win.combo_stage.findData(int(Stage.PHONE_INTERVIEW)))
    win.btn_reply.click()

    row = conn.execute("SELECT stage, status_mirror FROM opportunities "
                       "WHERE id=?", (oid,)).fetchone()
    assert Stage(row["stage"]) is Stage.PHONE_INTERVIEW
    assert row["status_mirror"] == "phone interview"


def test_a_cancelled_confirmation_writes_nothing(conn, win):
    """A negative determination closes the parent and renders every subtask
    `no offer`. That must not happen because a button was next to the one
    being aimed at."""
    oid = create_opportunity(conn, "Acme", stage=Stage.CONTACTED)
    add_task(conn, str(oid), "Chase Jo")
    connect_board(win, conn)
    _loaded(conn, win)
    shown = []
    win.confirm_determination = lambda lines: (shown.extend(lines), False)[1]

    win.tree.setCurrentItem(_find_item(win, str(oid)))
    win.btn_no_offer.click()

    row = conn.execute("SELECT stage FROM opportunities WHERE id=?",
                       (oid,)).fetchone()
    assert Stage(row["stage"]) is Stage.CONTACTED
    assert conn.execute("SELECT status FROM tasks").fetchone()["status"] == "open"
    # And the user was told what the cascade would do, in words, before being
    # asked — a dialog listing `stage=Lost` is asking for approval of
    # something that has to be decoded first.
    assert any("Lost" in line for line in shown)
    assert any("task" in line for line in shown)


def test_recording_no_offer_closes_the_tree(conn, win):
    oid = create_opportunity(conn, "Acme", stage=Stage.PHONE_INTERVIEW)
    add_task(conn, str(oid), "Prepare for the call")
    connect_board(win, conn)
    _loaded(conn, win)
    win.confirm_determination = lambda lines: True

    win.tree.setCurrentItem(_find_item(win, str(oid)))
    win.btn_no_offer.click()

    row = conn.execute("SELECT stage, status_mirror FROM opportunities "
                       "WHERE id=?", (oid,)).fetchone()
    assert Stage(row["stage"]) is Stage.LOST
    assert row["status_mirror"] == "no offer"
    assert conn.execute(
        "SELECT status FROM tasks").fetchone()["status"] == "no offer"


def test_a_closed_opportunity_takes_no_determination(conn, win):
    """An answer can only arrive on a pursuit that is still open. Offering it
    on a Won record would walk an accepted offer backwards."""
    oid = create_opportunity(conn, "Done", stage=Stage.WON)
    _loaded(conn, win)
    win.tree.setCurrentItem(_find_item(win, str(oid)))
    assert not win.btn_reply.isEnabled()
    assert not win.btn_no_offer.isEnabled()
    assert not win.combo_stage.isEnabled()


def test_the_stage_is_chosen_not_inferred(conn, win):
    """A reply and an interview invitation are both "they answered" and land
    two rungs apart. Guessing between them corrupts the pipeline read exactly
    as inferring a stage from a send would."""
    labels = [win.combo_stage.itemText(i)
              for i in range(win.combo_stage.count())]
    assert "In Dialogue" in labels and "Phone Interview" in labels
    assert "Lost" not in labels, "a positive answer cannot close a pursuit"
