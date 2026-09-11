"""Background work outlives the window that asked for it, and is then let go.

Closing Settings while its administrator check was still on the network
collected the window and, with it, the only reference to a task the thread pool
was still running. CI then died with an access violation in whichever test
happened to be pumping events next.
"""
import gc
import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import background  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_a_task_whose_caller_let_go_still_delivers(qapp, settle):
    release = threading.Event()
    got = []
    # No reference kept, exactly as when the window holding it has closed.
    background.run_in_background(lambda: release.wait(5) and "answer",
                                 on_done=got.append, on_error=got.append)
    gc.collect()
    release.set()
    settle(lambda: got, what="the result of a task nobody held")
    assert got == ["answer"]


def test_a_delivered_task_is_released(qapp, settle):
    release = threading.Event()
    got = []
    task = background.run_in_background(lambda: release.wait(5),
                                        on_done=got.append, on_error=got.append)
    assert task in background._UNDELIVERED, "positive control: held while running"
    release.set()
    settle(lambda: got, what="delivery")
    qapp.processEvents()
    assert task not in background._UNDELIVERED, "held for ever is a leak"


def test_a_failed_task_is_released_too(qapp, settle):
    got = []

    def broken():
        raise ValueError("no network")

    task = background.run_in_background(broken, on_done=got.append,
                                        on_error=got.append)
    settle(lambda: got, what="the failure")
    qapp.processEvents()
    assert isinstance(got[0], ValueError)
    assert task not in background._UNDELIVERED
