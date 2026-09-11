"""Settings covers the window it was opened from, so it must say how to leave.

Reported from use: once in Settings there was no way back to the shortlist.
The screen opens at nearly the size of the window beneath and hides it, and
every further press stacked another copy on top.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.ui.settings import SettingsWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_licence(monkeypatch):
    # With no stored licence the admin check returns before asking the Worker,
    # so opening a window here reaches nothing live.
    monkeypatch.setattr("app.core.entitlement.stored_licence", lambda: None)


def test_settings_shows_a_back_button(qapp):
    w = SettingsWindow(variant="direct")
    assert w.btn_back.text() == "Back"
    w.close()


def test_the_back_button_cannot_scroll_out_of_reach(qapp):
    w = SettingsWindow(variant="direct")
    # Positive control: the panels ARE inside the scroll area, so a False
    # below means the button is outside it, not that the check sees nothing.
    assert w.scroll.isAncestorOf(w.key)
    assert not w.scroll.isAncestorOf(w.btn_back)
    w.close()


def test_back_closes_settings_and_brings_the_opener_forward(qapp):
    home = QWidget()
    home.show()
    w = SettingsWindow(variant="direct", home=home)
    w.show()
    assert w.isVisible()
    w.btn_back.click()
    assert not w.isVisible()
    assert home.isVisible()
    home.close()


def test_back_with_no_opener_still_closes(qapp):
    """`--settings` opens the screen alone, with nothing to return to."""
    w = SettingsWindow(variant="direct")
    w.show()
    w.btn_back.click()
    assert not w.isVisible()


def test_a_second_press_raises_the_open_window_rather_than_stacking(qapp):
    from app.main import open_settings
    home = QWidget()
    first = open_settings(home)
    second = open_settings(home)
    assert second is first
    first.close()
    # Positive control: once closed, the next press builds a fresh window, so
    # the identity above is reuse and not a function that always returns one.
    third = open_settings(home)
    assert third is not first
    third.close()


def test_the_opener_is_passed_through_as_home(qapp):
    from app.main import open_settings
    home = QWidget()
    w = open_settings(home)
    assert w._home is home
    w.close()
