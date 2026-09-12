"""Closing the window: hide to the tray when asked to keep running, quit when not."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.core import db, schedule  # noqa: E402
from app.ui.tray import TrayPresence  # noqa: E402


class FakeController(QObject):
    changed = Signal()

    def __init__(self, offered=False):
        super().__init__()
        self.offered = offered
        self.runs = 0

    def offers_run_now(self):
        return self.offered

    def run_now(self):
        self.runs += 1
        return True

    def run_time(self):
        from datetime import time
        return time(7, 0)


@pytest.fixture()
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    # The tray turns this off for the whole application; no later test may
    # inherit that.
    QApplication.setQuitOnLastWindowClosed(True)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def presence(conn, *, available=True, offered=False):
    window = QWidget()
    quits = []
    tray = TrayPresence(window, conn, FakeController(offered),
                        available=lambda: available,
                        quit_app=lambda: quits.append(1))
    messages = []
    if tray.icon is not None:
        tray.icon.showMessage = lambda *args: messages.append(args)
    return window, tray, quits, messages


def test_keep_running_hides_the_window_instead_of_quitting(qapp, conn):
    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, True)
    window, tray, quits, messages = presence(conn)
    window.show()
    window.close()
    assert window.isHidden()
    assert quits == []
    assert not QApplication.quitOnLastWindowClosed()


def test_the_first_hide_says_so_once(qapp, conn):
    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, True)
    window, tray, quits, messages = presence(conn)
    window.show()
    window.close()
    assert len(messages) == 1
    assert "still running" in messages[0][0]
    window.show()
    window.close()
    assert len(messages) == 1, "said once, not on every close"


def test_without_keep_running_closing_quits_as_before(qapp, conn):
    window, tray, quits, messages = presence(conn)
    window.show()
    window.close()
    assert quits == [1]
    assert window.isHidden()
    assert messages == []


def test_with_no_tray_nothing_is_hidden_where_it_cannot_be_found(qapp, conn):
    """No tray means no way back to a hidden window, so keep-running cannot
    apply and Qt's own quit-on-last-window stays in charge."""
    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, True)
    window, tray, quits, messages = presence(conn, available=False)
    window.show()
    window.close()
    assert tray.icon is None
    assert quits == []
    assert QApplication.quitOnLastWindowClosed()


def test_the_tray_run_now_follows_the_rule(qapp, conn):
    window, tray, quits, messages = presence(conn)
    controller = tray._controller
    assert not tray.act_run.isEnabled()
    controller.offered = True
    controller.changed.emit()
    assert tray.act_run.isEnabled()
    tray.act_run.trigger()
    assert controller.runs == 1


def test_open_brings_the_window_back(qapp, conn):
    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, True)
    window, tray, quits, messages = presence(conn)
    window.show()
    window.close()
    tray.act_open.trigger()
    assert not window.isHidden()


def test_quit_from_the_tray_quits(qapp, conn):
    window, tray, quits, messages = presence(conn)
    tray.act_quit.trigger()
    assert quits == [1]


def test_a_launch_at_sign_in_stays_hidden_and_offers_from_the_tray(qapp, conn):
    window, tray, quits, messages = presence(conn, offered=True)
    assert tray.start_hidden() is True
    assert window.isHidden()
    assert "has not happened yet" in messages[0][1]


def test_a_launch_at_sign_in_with_nothing_to_offer_says_nothing(qapp, conn):
    window, tray, quits, messages = presence(conn, offered=False)
    assert tray.start_hidden() is True
    assert messages == []


def test_with_no_tray_a_launch_at_sign_in_opens_the_window(qapp, conn):
    window, tray, quits, messages = presence(conn, available=False)
    assert tray.start_hidden() is False


def test_the_launch_path_goes_to_the_tray_only_when_there_is_one(tmp_path, monkeypatch, qapp):
    import app.main as main_mod
    from PySide6.QtWidgets import QSystemTrayIcon

    path = tmp_path / "launch.sqlite3"
    c = db.connect(path)
    db.migrate(c)
    c.execute("INSERT INTO settings(key, value) "
              "VALUES('calibration_passed_at','2026-09-06T00:00:00+00:00')")
    c.commit()
    # The launch path checks both before it opens a window at all: an
    # unfinished setup goes back to the wizard, unagreed terms to the terms
    # gate, and this test is about neither.
    from app.onboarding import terms
    from app.onboarding.state import mark_setup_finished
    mark_setup_finished(c)
    terms.record_acceptance(c)
    monkeypatch.setattr("PySide6.QtWidgets.QApplication.exec", lambda self: 0)
    offered = []
    monkeypatch.setattr(main_mod, "_offer_update", offered.append)

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable",
                        staticmethod(lambda: False))
    main_mod._launch_ui(c, open_board=False, background=True)
    assert len(offered) == 1, "no tray: the window opens after all"
    offered[0]._daily_run[0].timer.stop()
    offered[0].close()

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable",
                        staticmethod(lambda: True))
    main_mod._launch_ui(c, open_board=False, background=True)
    assert len(offered) == 1, "a tray: stays hidden, and asks nothing"
    c.close()


def test_a_filter_outliving_its_window_stays_out_of_the_way(qapp, conn):
    """Qt goes on calling a filter whose C++ object has gone, and the raise
    lands wherever the event loop happens to be — it surfaced in an unrelated
    window's constructor, several tests away from the tray."""
    from PySide6.QtGui import QCloseEvent

    schedule.save_flag(conn, schedule.KEEP_RUNNING_KEY, True)
    window, tray, quits, messages = presence(conn)
    window.show()
    assert tray.eventFilter(window, QCloseEvent()) is True, (
        "while its window is alive the filter still decides the close")

    assert tray.eventFilter(QWidget(), QCloseEvent()) is False, (
        "another window's close is not this tray's to decide")

    del tray._window                         # every attribute destroyed
    assert tray.eventFilter(QWidget(), QCloseEvent()) is False
