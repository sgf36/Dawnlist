"""The Settings section that decides when Dawnlist runs."""
from datetime import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTime  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.sign_in import MAC_LOGIN_ITEMS, WINDOWS_STARTUP_APPS, Change  # noqa: E402
from app.ui.settings import SchedulePanel, SettingsWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def panel(qapp, *, run_time=time(7, 0), keep=False, sign_in_state=None,
          setter=None, tray_available=True):
    saved = {"time": run_time, "keep": keep, "asked": [], "pages": []}

    def set_sign_in(on):
        saved["asked"].append(on)
        return (setter or (lambda x: Change(x)))(on)

    p = SchedulePanel(
        loader=lambda: (saved["time"], saved["keep"]),
        time_saver=lambda value: saved.__setitem__("time", value),
        keep_saver=lambda on: saved.__setitem__("keep", on),
        sign_in_reader=(None if sign_in_state is None
                        else (lambda: sign_in_state)),
        sign_in_setter=set_sign_in,
        page_opener=lambda page: saved["pages"].append(page) or True,
        tray_available=tray_available)
    p._saved = saved
    return p


# -- the run time -----------------------------------------------------------

def test_it_opens_on_the_stored_run_time(qapp):
    p = panel(qapp, run_time=time(9, 30))
    assert p.time_field.time() == QTime(9, 30)
    assert p.result.isHidden(), "opening the screen is not a change"
    p.close()


def test_a_new_run_time_is_saved_and_read_back(qapp):
    p = panel(qapp)
    p.time_field.setTime(QTime(6, 15))
    assert p._saved["time"] == time(6, 15)
    assert "06:15" in p.result.text()
    p.close()


def test_changing_the_time_tells_whoever_is_keeping_it(qapp):
    """The window behind Settings has to re-ask whether Run now is offered."""
    p = panel(qapp)
    told = []
    p.changed.connect(lambda: told.append(True))
    p.time_field.setTime(QTime(8, 0))
    assert told == [True]
    p.close()


# -- keep running -----------------------------------------------------------

def test_keep_running_saves_both_ways_and_says_what_closing_now_does(qapp):
    p = panel(qapp)
    p.keep_running.setChecked(True)
    assert p._saved["keep"] is True
    assert "leave Dawnlist running" in p.result.text()
    p.keep_running.setChecked(False)
    assert p._saved["keep"] is False
    assert "quit Dawnlist" in p.result.text()
    p.close()


def test_with_no_tray_keep_running_is_refused_with_a_reason(qapp):
    p = panel(qapp, tray_available=False)
    assert not p.keep_running.isEnabled()
    p.close()


# -- start at sign in -------------------------------------------------------

def test_a_build_that_cannot_do_it_shows_no_switch(qapp):
    p = panel(qapp, sign_in_state=None)
    assert p.sign_in.isHidden()
    p.close()


def test_the_switch_shows_what_the_system_says(qapp, settle):
    p = panel(qapp, sign_in_state=True)
    settle(lambda: p.sign_in.isChecked(), what="the sign-in state")
    assert p.sign_in.isChecked()
    p.close()


def test_switching_on_asks_the_system_and_confirms(qapp, settle):
    p = panel(qapp, sign_in_state=False)
    settle(lambda: p.sign_in.isEnabled(), what="the first read")
    p.sign_in.setChecked(True)
    settle(lambda: p._saved["asked"], what="the switch")
    settle(lambda: "sign in" in p.result.text(), what="the answer")
    assert p._saved["asked"] == [True]
    assert p.sign_in.isChecked()
    assert p.result.text() == "Dawnlist will start in the background when you sign in."
    p.close()


def test_a_refusal_unticks_the_box_and_opens_the_page_the_user_needs(qapp, settle):
    """A tick left on while nothing was switched on is the one outcome
    nobody can act on."""
    p = panel(qapp, sign_in_state=False,
              setter=lambda on: Change(False, open_page=WINDOWS_STARTUP_APPS))
    settle(lambda: p.sign_in.isEnabled(), what="the first read")
    p.sign_in.setChecked(True)
    settle(lambda: p._saved["pages"], what="the page")
    assert p._saved["pages"] == [WINDOWS_STARTUP_APPS]
    assert not p.sign_in.isChecked()
    assert "Startup apps" in p.result.text()
    p.close()


def test_an_approval_on_macos_names_login_items(qapp, settle):
    p = panel(qapp, sign_in_state=False,
              setter=lambda on: Change(False, open_page=MAC_LOGIN_ITEMS))
    settle(lambda: p.sign_in.isEnabled(), what="the first read")
    p.sign_in.setChecked(True)
    settle(lambda: p._saved["pages"], what="the page")
    assert "Login Items" in p.result.text()
    p.close()


def test_an_administrators_decision_is_explained(qapp, settle):
    p = panel(qapp, sign_in_state=False,
              setter=lambda on: Change(False, problem="policy"))
    settle(lambda: p.sign_in.isEnabled(), what="the first read")
    p.sign_in.setChecked(True)
    settle(lambda: "manages this computer" in p.result.text(), what="the answer")
    assert not p.sign_in.isChecked()
    p.close()


def test_a_failure_names_itself_rather_than_going_quiet(qapp, settle):
    def explode(on):
        raise OSError("access denied")

    p = panel(qapp, sign_in_state=False, setter=explode)
    settle(lambda: p.sign_in.isEnabled(), what="the first read")
    p.sign_in.setChecked(True)
    settle(lambda: "Could not change" in p.result.text(), what="the failure")
    assert "access denied" in p.result.text()
    assert not p.sign_in.isChecked()
    p.close()


# -- in the window ----------------------------------------------------------

def test_the_window_carries_the_panel_when_given_one(qapp):
    p = panel(qapp)
    w = SettingsWindow(variant="direct", schedule=p)
    assert w.schedule is p
    assert p.parent() is not None, "it is laid out, not merely held"
    w.close()


def test_the_window_omits_it_without_a_database(qapp):
    w = SettingsWindow(variant="direct")
    assert w.schedule is None
    w.close()
