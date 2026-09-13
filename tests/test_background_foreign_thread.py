"""Work started from a thread Qt does not run must still answer on the UI thread.

Measured on the cloud Mac against build 170, 2026-09-13: StoreKit delivered a
PURCHASED transaction to the observer, the observer started the Worker exchange
with `run_in_background`, and the answer never arrived. The signals object was
owned by the StoreKit callback thread, whose event loop never turns, so the
queued `done` signal was never delivered. The transaction was therefore never
finished, Apple redelivered it at every launch, and every later Subscribe press
was discarded as "already in the SKPaymentQueue".
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, until, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.02)


def test_work_started_from_a_foreign_thread_answers_on_the_ui_thread(qapp):
    from app.ui.background import run_in_background

    answers, answered_on_main = [], []

    def done(value):
        answers.append(value)
        answered_on_main.append(threading.current_thread() is threading.main_thread())

    starter = threading.Thread(
        target=lambda: run_in_background(lambda: "licence", on_done=done,
                                         on_error=done))
    starter.start()
    starter.join()
    _pump(qapp, lambda: answers)
    assert answers == ["licence"]
    assert answered_on_main == [True]


def test_work_started_on_the_ui_thread_still_answers(qapp):
    """The positive control: the ordinary path is unchanged."""
    from app.ui.background import run_in_background

    answers = []
    run_in_background(lambda: 7, on_done=answers.append, on_error=answers.append)
    _pump(qapp, lambda: answers)
    assert answers == [7]
