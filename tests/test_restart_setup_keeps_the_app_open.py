"""Start setup again must not end the application.

Reported by Spencer on 2026-09-13, on the Microsoft Store build: pressing
"Start setup again" closed Dawnlist outright. The restart closes the shortlist
window after opening setup, and the tray presence watches that window's close:
with "keep running" off it reads any close as the user quitting and ends the
process, taking the setup window with it. The offscreen smoke run had no system
tray, so it never saw this; the test forces a tray to exist.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    QApplication.setQuitOnLastWindowClosed(True)


def _pump(seconds=0.5):
    from PySide6.QtWidgets import QApplication

    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


@pytest.mark.parametrize("keep_running", [False, True])
@pytest.mark.parametrize("build", ["store_iap", "mas", "direct"])
def test_start_setup_again_opens_setup_and_does_not_quit(qapp, monkeypatch, tmp_path,
                                                         build, keep_running):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app import main
    from app.core import db, schedule
    from app.onboarding import state

    monkeypatch.setattr("app.core.build_variant.variant", lambda: build)
    monkeypatch.setattr("app.core.entitlement.stored_licence", lambda: None)
    monkeypatch.setattr("app.ui.tray.QSystemTrayIcon.isSystemTrayAvailable",
                        staticmethod(lambda: True))
    quits = []
    monkeypatch.setattr("app.ui.tray.QApplication.quit", staticmethod(lambda: quits.append(1)))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    conn = db.connect(tmp_path / "d.sqlite3")
    db.migrate(conn)
    state.mark_setup_finished(conn)
    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, keep_running)

    held = len(main._HELD_WINDOWS)
    window = main._main_window(conn, open_board=False)
    window.show()
    _pump()

    window.restart_setup_requested.emit()
    _pump(1.0)

    assert not quits, "Start setup again ended the application"
    wizards = [w for w in main._HELD_WINDOWS[held:] if hasattr(w, "fit_to_screen")]
    assert wizards and wizards[-1].isVisible(), "setup did not open"
    # With the shortlist gone and only setup open, closing setup must end the
    # app as it does on a first launch, not leave an invisible process behind.
    assert QApplication.quitOnLastWindowClosed()
    for wizard in wizards:
        wizard._finishing = True
        wizard.close()
    del main._HELD_WINDOWS[held:]
