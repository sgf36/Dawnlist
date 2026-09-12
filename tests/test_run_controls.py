"""Run now, the last-run line and the run's status, on the review window.

The controller is driven with an injected clock and an injected `work`, so
nothing here fetches a posting or reaches Anthropic.
"""
import threading
from datetime import datetime, time, timedelta, timezone

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import db, schedule  # noqa: E402
from app.core.api_key import KeyProblem  # noqa: E402
from app.core.run_report import COMPLETE, KEY, LIMIT, RunReport  # noqa: E402
from app.ui.review import ReviewWindow  # noqa: E402
from app.ui.scheduler import (RunBinding, RunController, describe_report,  # noqa: E402
                              format_clock, format_when, last_run_text,
                              status_text)

UTC = timezone.utc


def as_utc(moment):
    return moment.astimezone(UTC)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


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
    w = ReviewWindow()
    yield w
    w.close()


def add_run(conn, started_at, *, status="complete", kind="sweep",
            note=None, error=None, swept=0):
    conn.execute(
        "INSERT INTO runs(started_at, status, kind, incomplete_note, "
        "fetch_error, swept) VALUES(?,?,?,?,?,?)",
        (started_at, status, kind, note, error, swept))
    conn.commit()


# ---------------------------------------------------------------------------
# The window on its own
# ---------------------------------------------------------------------------

def test_run_now_starts_hidden_and_disabled(win):
    assert win.btn_run_now.isHidden()
    assert not win.btn_run_now.isEnabled()


def test_run_now_shows_exactly_when_offered(win):
    win.set_run_state(offered=True, running=False)
    assert not win.btn_run_now.isHidden() and win.btn_run_now.isEnabled()
    win.set_run_state(offered=False, running=False)
    assert win.btn_run_now.isHidden() and not win.btn_run_now.isEnabled()


def test_a_running_search_hides_run_now_and_shows_progress(win):
    win.set_run_state(offered=True, running=True)
    assert win.btn_run_now.isHidden() and not win.btn_run_now.isEnabled()
    assert not win.run_busy.isHidden() and not win.run_progress.isHidden()
    win.set_run_state(offered=False, running=False)
    assert win.run_busy.isHidden()


def test_pressing_run_now_asks_rather_than_runs(win):
    asked = []
    win.run_now_requested.connect(lambda: asked.append(True))
    win.set_run_state(offered=True, running=False)
    win.btn_run_now.click()
    assert asked == [True]


def test_an_empty_status_takes_no_space(win):
    win.set_run_status("The last run failed: boom")
    assert not win.run_status.isHidden()
    win.set_run_status("")
    assert win.run_status.isHidden()


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

def controller(conn, clock, work, run_time="07:00"):
    schedule.save_run_time(conn, schedule.parse_run_time(run_time))
    return RunController(conn, work=work, clock=clock, to_local=as_utc)


def test_the_run_starts_when_its_time_arrives_while_open(qapp, conn, settle):
    clock = Clock(datetime(2026, 9, 11, 6, 0, tzinfo=UTC))
    calls = []
    ctl = controller(conn, clock, lambda: calls.append(1) or RunReport(COMPLETE))
    reports = []
    ctl.finished.connect(reports.append)

    ctl.start()
    assert calls == [] and not ctl.running, "not before its time"

    clock.now = datetime(2026, 9, 11, 7, 0, 20, tzinfo=UTC)
    ctl.tick()
    settle(lambda: reports, what="the scheduled run")
    assert calls == [1]
    assert reports[0].kind == COMPLETE
    assert not ctl.running

    ctl.tick()
    ctl.tick()
    assert calls == [1], "once per day, however many ticks follow"
    ctl.timer.stop()


def test_launching_after_the_run_time_offers_and_spends_nothing(qapp, conn, settle):
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
    calls = []
    ctl = controller(conn, clock, lambda: calls.append(1) or RunReport(COMPLETE))
    ctl.start()
    clock.now += timedelta(minutes=10)
    ctl.tick()
    assert calls == []
    assert ctl.offers_run_now()
    ctl.timer.stop()


def test_run_now_is_refused_before_the_run_time(qapp, conn):
    clock = Clock(datetime(2026, 9, 11, 6, 0, tzinfo=UTC))
    ctl = controller(conn, clock, lambda: RunReport(COMPLETE))
    assert not ctl.offers_run_now()
    assert ctl.run_now() is False
    assert not ctl.running


def test_run_now_is_refused_once_a_search_has_started_today(qapp, conn):
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
    ctl = controller(conn, clock, lambda: RunReport(COMPLETE))
    assert ctl.offers_run_now()
    add_run(conn, "2026-09-11T07:00:05+00:00")
    assert not ctl.offers_run_now()
    assert ctl.run_now() is False


def test_no_second_run_while_one_is_running(qapp, conn, settle):
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
    release = threading.Event()
    calls = []

    def work():
        calls.append(1)
        release.wait(5)
        return RunReport(COMPLETE)

    ctl = controller(conn, clock, work)
    done = []
    ctl.finished.connect(done.append)
    try:
        assert ctl.run_now() is True
        assert ctl.running
        assert not ctl.offers_run_now()
        assert ctl.run_now() is False
        ctl.tick()
    finally:
        release.set()
    settle(lambda: done, what="the manual run")
    assert calls == [1]


def test_a_refused_run_is_reported_in_its_own_terms(qapp, conn, settle):
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))

    def work():
        raise KeyProblem("Dawnlist needs your own Anthropic API key")

    ctl = controller(conn, clock, work)
    reports = []
    ctl.finished.connect(reports.append)
    ctl.run_now()
    settle(lambda: reports, what="the refused run")
    assert reports[0].kind == KEY
    assert not ctl.running
    assert ctl.offers_run_now(), "nothing was spent, so it is still offered"


def test_a_second_copy_cannot_take_the_same_scheduled_run(qapp, conn, settle):
    clock = Clock(datetime(2026, 9, 11, 6, 0, tzinfo=UTC))
    calls = []
    first = controller(conn, clock, lambda: calls.append("a") or RunReport(COMPLETE))
    second = RunController(conn, work=lambda: calls.append("b") or RunReport(COMPLETE),
                           clock=clock, to_local=as_utc)
    done = []
    first.finished.connect(done.append)
    second.finished.connect(done.append)
    clock.now = datetime(2026, 9, 11, 7, 0, 1, tzinfo=UTC)
    first.tick()
    second.tick()
    settle(lambda: done, what="the scheduled run")
    assert calls == ["a"]


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

def test_the_last_run_line_uses_local_time_and_says_when_not_today(qapp):
    started = "2026-09-11T06:02:00+00:00"
    plus_one = timezone(timedelta(hours=1))
    local = datetime(2026, 9, 11, 7, 2)
    today = last_run_text(started, started_today=True,
                          to_local=lambda m: m.astimezone(plus_one))
    assert format_when(local) in today
    assert "no run yet today" not in today

    later = last_run_text(started, started_today=False,
                          to_local=lambda m: m.astimezone(plus_one))
    assert format_when(local) in later and "no run yet today" in later
    assert last_run_text(None, started_today=False) == "No run yet"


def test_the_limit_names_its_number_and_when_it_resets(qapp):
    plus_one = timezone(timedelta(hours=1))
    text = describe_report(
        RunReport(LIMIT, detail="Daily refresh cap reached (3)",
                  refreshes_per_day=3),
        now=datetime(2026, 9, 11, 10, 0, tzinfo=plus_one),
        to_local=lambda m: m.astimezone(plus_one))
    assert "3 refreshes" in text and "UTC" in text
    assert format_clock(time(1, 0)) in text


def test_status_text_reports_a_failed_or_partial_run_once(qapp, conn):
    from app.ui.adapter import run_status

    add_run(conn, "2026-09-11T06:00:00+00:00", status="incomplete",
            note="strategy: Could not reach the Dawnlist feed service.",
            error="strategy: Could not reach the Dawnlist feed service.")
    text = status_text(run_status(conn, 1))
    assert text.startswith("The last run did not read everything")
    assert text.count("Could not reach") == 1

    add_run(conn, "2026-09-11T06:00:00+00:00", status="complete")
    assert status_text(run_status(conn, 2)) == ""


# ---------------------------------------------------------------------------
# The binding
# ---------------------------------------------------------------------------

def bound(win, conn, clock, work=None, notify=None):
    ctl = controller(conn, clock, work or (lambda: RunReport(COMPLETE)))
    reloads = []
    binding = RunBinding(win, conn, ctl, reload=lambda: reloads.append(1),
                         notify=notify, clock=clock, to_local=as_utc)
    return ctl, binding, reloads


def test_a_failed_latest_run_is_shown_not_hidden(win, conn):
    add_run(conn, "2026-09-10T06:00:00+00:00", swept=40)
    add_run(conn, "2026-09-11T06:00:00+00:00", status="failed",
            note="RuntimeError: the feed fell over")
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
    _ctl, binding, reloads = bound(win, conn, clock)
    binding.show_latest()
    assert reloads == [1]
    assert not win.run_status.isHidden()
    assert "The last run failed" in win.run_status.text()
    assert "the feed fell over" in win.run_status.text()


def test_the_offer_is_explained_when_the_run_time_has_passed(win, conn):
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
    _ctl, binding, _ = bound(win, conn, clock)
    binding.show_latest()
    assert not win.btn_run_now.isHidden()
    assert "has not happened yet" in win.run_status.text()
    assert win.last_run.text() == "No run yet"


def test_nothing_is_offered_before_the_run_time(win, conn):
    clock = Clock(datetime(2026, 9, 11, 6, 0, tzinfo=UTC))
    _ctl, binding, _ = bound(win, conn, clock)
    binding.show_latest()
    assert win.btn_run_now.isHidden()
    assert win.run_status.isHidden()


def test_finishing_reloads_and_names_the_limit(qapp, win, conn, settle):
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
    report = RunReport(LIMIT, refreshes_per_day=3)
    ctl, binding, reloads = bound(win, conn, clock, work=lambda: report)
    binding.show_latest()
    ctl.run_now()
    settle(lambda: len(reloads) == 2, what="the reload after the run")
    assert "3 refreshes" in win.run_status.text()
    ctl.tick()
    assert "3 refreshes" in win.run_status.text(), (
        "the next tick must not replace the run's own explanation")


def test_a_hidden_window_hears_about_the_run_through_notify(qapp, win, conn, settle):
    clock = Clock(datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
    told = []
    ctl, binding, reloads = bound(win, conn, clock, notify=told.append)
    ctl.run_now()
    settle(lambda: told, what="the notification")
    assert told == ["Today's run has finished and the shortlist is up to date."]

    # Positive control: a window on screen says it itself.
    win.show()
    told.clear()
    conn.execute("DELETE FROM settings WHERE key=?", (schedule.SCHEDULED_CLAIM_KEY,))
    ctl.run_now()
    settle(lambda: len(reloads) == 2, what="the second run")
    assert told == []


# ---------------------------------------------------------------------------
# The launch
# ---------------------------------------------------------------------------

def test_the_launched_window_offers_run_now_after_the_run_time(tmp_path, monkeypatch, qapp):
    import app.main as main_mod

    path = tmp_path / "launch.sqlite3"
    c = db.connect(path)
    db.migrate(c)
    c.execute("INSERT INTO settings(key, value) "
              "VALUES('calibration_passed_at','2026-09-06T00:00:00+00:00')")
    c.commit()
    # Midnight, so the run time has passed whenever this test runs.
    schedule.save_run_time(c, time(0, 0))
    # The launch path checks both before it opens a window at all: an
    # unfinished setup goes back to the wizard, unagreed terms to the terms
    # gate, and this test is about neither.
    from app.onboarding import terms
    from app.onboarding.state import mark_setup_finished
    mark_setup_finished(c)
    terms.record_acceptance(c)

    shown = []
    monkeypatch.setattr(main_mod, "_offer_update", shown.append)
    monkeypatch.setattr("PySide6.QtWidgets.QApplication.exec", lambda self: 0)
    main_mod._launch_ui(c, open_board=False)
    window = shown[0]
    assert not window.btn_run_now.isHidden()

    # Positive control: a search started now takes the offer away.
    add_run(c, datetime.now(UTC).isoformat(timespec="seconds"))
    controller, _binding, _tray = window._daily_run
    controller.tick()
    assert window.btn_run_now.isHidden()
    controller.timer.stop()
    window.close()
    c.close()
