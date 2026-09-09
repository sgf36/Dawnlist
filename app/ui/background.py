"""Run slow work off the UI thread, and deliver the result back onto it.

WHY THIS EXISTS
---------------
Nothing in `app/ui/` used a thread. Every slow operation — reading a corpus of
CVs, calling Anthropic, fetching live postings, verifying an API key — ran
inside the Qt event loop, so the window stopped painting and stopped answering
the OS. Windows then titles it "(Not Responding)", which is what a person sees
and what a Store reviewer sees.

That is not only a bad experience. Microsoft Store Policy 10.4.2 requires that
products "continue to run and remain responsive to user input", so a screen
that freezes for the length of an API call is a certification risk on a
submission that is in certification right now.

The onboarding draft screen had a `self.status.repaint()` immediately before
the blocking call — somebody knew the freeze was coming and forced one last
paint instead of moving the work. Getting the "please wait" text on screen
before you stop responding is not the same as not stopping.

HOW TO USE IT
-------------
    self._task = run_in_background(
        lambda: expensive(a, b),
        on_done=self._finished,        # called ON THE UI THREAD
        on_error=self._failed,         # called ON THE UI THREAD
    )

KEEP THE RETURNED HANDLE. Qt objects with no Python reference are collected,
and a collected task never delivers. Assigning it to `self` is enough.

WHAT THIS DOES NOT DO
---------------------
It does not cancel. A half-written draft is cheap to throw away and the
callbacks check that the widget is still alive, so cancellation buys little and
costs a whole class of race. If a caller genuinely needs it, add it there
rather than making every caller carry the machinery.

IT DOES NOT MAKE THE WORK THREAD-SAFE. `fn` runs on a worker thread and must
not touch Qt widgets or a sqlite3 connection created on the UI thread — sqlite
connections belong to the thread that opened them. Pass plain data in, return
plain data out.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Signals(QObject):
    """Signals live on a QObject; QRunnable is not one."""

    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self._fn = fn
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001
            # EVERY exception, including ones a caller did not anticipate. An
            # exception escaping a QRunnable is swallowed by the thread pool
            # and the UI waits for a callback that will never come — a hang
            # that looks exactly like slow work.
            self._emit(self.signals.failed, exc)
            return
        self._emit(self.signals.done, result)

    @staticmethod
    def _emit(signal, payload) -> None:
        """Deliver the result, unless the thing waiting for it has gone.

        A window closed while its task is still running takes its receiver with
        it, and Qt then raises `RuntimeError: Signal source has been deleted`
        FROM THE WORKER THREAD, where nothing is catching it. The work has
        finished and nobody is left to care about the answer, so dropping it is
        the correct response — but it has to be dropped deliberately rather
        than as an unhandled error printed from a thread.

        Not hypothetical: closing Settings while the administrator check was in
        flight raised it every time, and a long draft would do the same.
        """
        try:
            signal.emit(payload)
        except RuntimeError:
            pass


def run_in_background(fn: Callable[[], Any], *,
                      on_done: Callable[[Any], None],
                      on_error: Callable[[BaseException], None]) -> _Task:
    """Run `fn` on a worker thread. Returns the task — KEEP A REFERENCE."""
    task = _Task(fn)
    # Queued by default across threads, so both callbacks arrive on the UI
    # thread and may touch widgets safely.
    task.signals.done.connect(on_done)
    task.signals.failed.connect(on_error)
    QThreadPool.globalInstance().start(task)
    return task
