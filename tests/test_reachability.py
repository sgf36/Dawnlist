"""Every part of the app a customer is sold can be reached from the app itself.

Found by audit on 2026-09-13, on every shipped build:

  * the board opened only with `--board` on a command line, so a posting marked
    Pursue went somewhere no Store or Mac customer could ever open again;
  * drafting follow-ups ran only as `--draft`, while every store listing said
    Dawnlist "drafts your follow-ups";
  * the menu showed "Settings" and "Dawnlist — settings", the same command
    twice, and nothing on the window itself led to Settings;
  * there was no way to go back through setup.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(until, seconds=3.0):
    from PySide6.QtWidgets import QApplication

    end = time.time() + seconds
    while time.time() < end and not until():
        QApplication.processEvents()
        time.sleep(0.01)


@pytest.mark.parametrize("build", ["mas", "store", "store_iap", "direct"])
def test_settings_appears_once_in_the_menu_on_every_build(qapp, monkeypatch, build):
    from app.i18n import tr
    from app.ui.review import ReviewWindow

    monkeypatch.setattr("app.core.build_variant.variant", lambda: build)
    window = ReviewWindow()
    menu = window.menuBar().actions()[0].menu()
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts.count(tr("menu.settings")) == 1
    assert tr("settings.title") not in texts
    assert tr("menu.board") in texts
    assert tr("menu.restart_setup") in texts


def test_the_window_itself_leads_to_settings_and_the_board(qapp):
    from app.ui.review import ReviewWindow

    window = ReviewWindow()
    seen = []
    window.settings_requested.connect(lambda: seen.append("settings"))
    window.board_requested.connect(lambda: seen.append("board"))
    window.btn_settings.click()
    window.btn_board.click()
    window.act_board.trigger()
    assert seen == ["settings", "board", "board"]


def test_the_board_opens_from_the_shortlist_and_is_reused(qapp, tmp_path):
    from app import main
    from app.core import db
    from app.ui.review import ReviewWindow

    conn = db.connect(tmp_path / "d.sqlite3")
    db.migrate(conn)
    parent = ReviewWindow()
    first = main._open_board(parent, conn)
    again = main._open_board(parent, conn)
    assert first is again
    assert first.isVisible()
    first.close()


def test_the_board_offers_follow_up_drafting(qapp):
    from app.ui.board import BoardWindow

    board = BoardWindow()
    asked = []
    board.drafts_requested.connect(lambda: asked.append(True))
    board.btn_drafts.click()
    assert asked == [True]


def test_drafting_runs_off_the_ui_thread_and_reports(qapp, monkeypatch, tmp_path):
    import threading

    from app import main
    from app.outreach.run import OutreachReport
    from app.ui.board import BoardWindow

    ran_on = []

    def fake_outreach(conn):
        ran_on.append(threading.current_thread())
        return OutreachReport()

    shown = []
    monkeypatch.setattr(main, "outreach_run", fake_outreach)
    monkeypatch.setattr(main, "database_path", lambda conn: str(tmp_path / "d.sqlite3"))
    monkeypatch.setattr("app.ui.board_adapter.board_rows", lambda conn: ([], {}))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information",
                        lambda *a, **k: shown.append(a[2]))
    board = BoardWindow()
    monkeypatch.setattr(board, "load", lambda *a, **k: None)
    main._draft_followups(board, conn=None)
    _pump(lambda: shown)
    assert ran_on and ran_on[0] is not threading.main_thread()
    from app.i18n import tr
    assert shown == [tr("board.drafts_none")]
    assert board.btn_drafts.isEnabled()


def test_restarting_setup_forgets_only_the_setup_markers(tmp_path):
    from app import main
    from app.core import db
    from app.onboarding import calibration, state, terms

    conn = db.connect(tmp_path / "d.sqlite3")
    db.migrate(conn)
    for key in (state.SETUP_FINISHED_KEY, state.DRAFT_KEY, calibration.CALIBRATION_KEY,
                terms.ACCEPTED_VERSION_KEY, terms.ACCEPTED_AT_KEY, "locale", "run_time"):
        conn.execute("INSERT INTO settings(key, value) VALUES(?, 'x') "
                     "ON CONFLICT(key) DO UPDATE SET value='x'", (key,))
    conn.commit()
    assert state.is_setup_finished(conn)

    main.restart_setup(conn)

    assert not state.is_setup_finished(conn)
    left = {r["key"] for r in conn.execute("SELECT key FROM settings")}
    assert {"locale", "run_time"} <= left, "the user's own choices stay"
