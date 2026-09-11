"""Keeping the daily run's time while Dawnlist is open, and saying how it went.

`RunController` owns the clock and the only in-process right to start a run.
`RunBinding` puts its state on the review window. Neither knows how a run is
performed: that is `work`, which in the app is `run_daily_search_on_worker` —
the same door `--run-once` goes through.

WHY A TIMER AND NOT A SINGLE SHOT
---------------------------------
A timer set for "07:00 tomorrow" is wrong after a laptop sleeps through it,
after the clocks change and after the user moves the run time. Asking the rule
every half-minute costs one small query and is right in all three cases: on
waking at 09:10, the first tick finds the run time passed while the app was
open and runs.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Callable

from PySide6.QtCore import QDate, QDateTime, QLocale, QObject, QTime, QTimer, Signal

from app.core import schedule
from app.core.run_report import (COMPLETE, ERROR, FETCH_FAILED, KEY, LIMIT,
                                 NOT_CONFIGURED, NOT_ENTITLED, PARTIAL,
                                 RunReport, report_from_error)
from app.i18n import current_locale, tr
from app.ui.background import run_in_background

#: Half a minute: a run starts within 30 seconds of its time, which nobody
#: waiting for a morning shortlist can perceive, and the query behind each tick
#: reads at most two days of `runs`.
TICK_MS = 30_000


class RunController(QObject):
    #: Anything that could change whether Run now is offered.
    changed = Signal()
    #: A run ended. Carries a RunReport.
    finished = Signal(object)

    def __init__(self, conn, *, work: Callable[[], RunReport],
                 clock: Callable[[], datetime] | None = None,
                 to_local: schedule.ToLocal | None = None,
                 parent=None):
        super().__init__(parent)
        self._conn = conn
        self._work = work
        self._clock = clock = clock or schedule.local_now
        self._to_local = to_local or schedule.system_local
        #: A run time that passed before this moment passed while the app was
        #: closed, and is offered rather than run. See `schedule.is_due`.
        self._watching_since = clock()
        self._running = False
        self._task = None
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.tick)

    def start(self) -> None:
        self.timer.start()
        self.tick()

    @property
    def running(self) -> bool:
        return self._running

    def run_time(self) -> time:
        return schedule.load_run_time(self._conn)

    def started_today(self) -> bool:
        return schedule.run_started_today(self._conn, self._clock(),
                                          self._to_local)

    def offers_run_now(self) -> bool:
        if self._running:
            return False
        return schedule.should_offer_run_now(
            self._clock(), self.run_time(), started_today=self.started_today())

    def tick(self) -> None:
        if not self._running:
            now = self._clock()
            due = schedule.is_due(
                now, self.run_time(), started_today=self.started_today(),
                watching_since=self._watching_since,
                claimed_on=schedule.claimed_on(self._conn))
            # Claimed before starting, and only then: a second copy of the app
            # ticking in the same second loses the claim and stands down.
            if due and schedule.claim_scheduled_run(self._conn, now.date()):
                self._start()
                return
        # Emitted every tick, because the clock passing the run time is itself
        # what makes Run now appear on a window left open.
        self.changed.emit()

    def run_now(self) -> bool:
        """Start a run by hand, if the rule allows one. False if it does not.

        Re-checked here rather than trusted from the button, which may be a
        tick out of date: a second press during the frame before it hid, or a
        run another copy started a moment ago.
        """
        if not self.offers_run_now():
            return False
        self._start()
        return True

    def _start(self) -> None:
        self._running = True
        self.changed.emit()
        self._task = run_in_background(self._work, on_done=self._done,
                                       on_error=self._failed)

    def _done(self, report) -> None:
        self._running = False
        self._task = None
        self.finished.emit(report)
        self.changed.emit()

    def _failed(self, exc: BaseException) -> None:
        self._done(report_from_error(exc))


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

def _qlocale() -> QLocale:
    # The app's own language, not the machine's: a person who chose French on
    # an English Windows reads French dates everywhere else in Dawnlist.
    return QLocale(current_locale())


def format_when(moment: datetime) -> str:
    stamp = QDateTime(QDate(moment.year, moment.month, moment.day),
                      QTime(moment.hour, moment.minute))
    return _qlocale().toString(stamp, QLocale.FormatType.ShortFormat)


def format_clock(value: time | datetime) -> str:
    return _qlocale().toString(QTime(value.hour, value.minute),
                               QLocale.FormatType.ShortFormat)


def last_run_text(started_at: str | None, *, started_today: bool,
                  to_local: schedule.ToLocal = schedule.system_local) -> str:
    if not started_at:
        return tr("run.never")
    moment = datetime.fromisoformat(started_at)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    when = format_when(to_local(moment))
    return tr("run.last" if started_today else "run.last_not_today", when=when)


def status_text(status) -> str:
    """PIPELINE-P6: what the latest run's row says went wrong, or nothing."""
    if status is None:
        return ""
    if status.status == "running":
        return tr("run.in_progress_status")
    if not status.went_wrong:
        return ""
    notes = []
    for text in (status.incomplete_note, status.fetch_error):
        # The pipeline writes the same fetch failure into both columns; saying
        # it twice makes one problem read as two.
        if text and all(text not in n and n not in text for n in notes):
            notes.append(text)
    note = "; ".join(notes) or tr("run.no_detail")
    key = "run.failed_status" if status.status == "failed" else "run.incomplete_status"
    return tr(key, note=note)


def describe_report(report: RunReport, *, now: datetime,
                    to_local: schedule.ToLocal = schedule.system_local) -> str:
    kind = report.kind
    if kind == COMPLETE:
        return tr("run.done")
    if kind == LIMIT:
        reset = schedule.next_utc_midnight(now, to_local)
        return tr("run.limit_reached", count=report.refreshes_per_day,
                  reset=format_clock(reset))
    key = {PARTIAL: "run.partial", FETCH_FAILED: "run.fetch_failed",
           NOT_CONFIGURED: "run.not_configured",
           NOT_ENTITLED: "run.not_entitled", KEY: "run.key_problem",
           ERROR: "run.error"}.get(kind, "run.error")
    return tr(key, reason=report.detail or tr("run.no_detail"))


# ---------------------------------------------------------------------------
# The review window
# ---------------------------------------------------------------------------

class RunBinding(QObject):
    """Keeps the review window's run controls true to the controller.

    `reload` puts the latest run's rows and funnel on the window; it lives in
    `app.main`, beside the functions that build them.
    """

    def __init__(self, window, conn, controller: RunController, *,
                 reload: Callable[[], None],
                 notify: Callable[[str], None] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 to_local: schedule.ToLocal | None = None,
                 parent=None):
        super().__init__(parent or window)
        self._window = window
        self._conn = conn
        self._controller = controller
        self._reload = reload
        self._notify = notify
        self._clock = clock or schedule.local_now
        self._to_local = to_local or schedule.system_local
        #: Set when a run's own report is on screen, so the next tick does not
        #: replace "the daily limit was reached" with the stored row's rawer
        #: English note until something new happens.
        self._report_shown = ""
        controller.changed.connect(self.refresh_state)
        controller.finished.connect(self.finished)
        window.run_now_requested.connect(controller.run_now)

    def show_latest(self) -> None:
        """Rows, funnel and the stored status of the latest run."""
        from app.ui.adapter import latest_run_id, run_status

        self._reload()
        self._report_shown = ""
        self._window.set_run_status(
            status_text(run_status(self._conn, latest_run_id(self._conn))))
        self.refresh_state()

    def refresh_state(self) -> None:
        controller = self._controller
        offered = controller.offers_run_now()
        self._window.set_run_state(offered=offered, running=controller.running)
        self._window.set_last_run(last_run_text(
            schedule.last_search_started_at(self._conn),
            started_today=controller.started_today(), to_local=self._to_local))

        window = self._window
        showing_problem = (window.run_status.objectName() == "runStatus"
                           and bool(window.run_status.text()))
        if not showing_problem and not self._report_shown:
            window.set_run_status(
                tr("run.offer", time=format_clock(controller.run_time()))
                if offered else "", problem=False)

    def finished(self, report: RunReport) -> None:
        self.show_latest()
        if not report.ok:
            text = describe_report(report, now=self._clock(),
                                   to_local=self._to_local)
            self._window.set_run_status(text)
            self._report_shown = text
        if self._notify is not None and not self._window.isVisible():
            # Hidden in the tray, the window cannot say it; the tray can.
            self._notify(describe_report(report, now=self._clock(),
                                         to_local=self._to_local))
