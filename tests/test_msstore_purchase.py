"""The Microsoft Store purchase: owner window, UI thread, and the module itself.

Found by audit on 2026-09-13, none of it covered by any test before:

  * `winrt-Windows.Services.Store` was never installed, so on the live Store
    build every call raised ImportError and Subscribe could never work;
  * the purchase started on a worker thread with no owner window, which
    Microsoft documents as failing with 0x80070578 for a desktop app
    (StoreContext.RequestPurchaseAsync, Remarks; "Using the StoreContext class
    with the Desktop Bridge").
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

from app.core import msstore


class _Op:
    def __init__(self, status):
        self.status_value, self.got_on = status, None

    def get(self):
        self.got_on = threading.current_thread()
        return type("R", (), {"status": self.status_value})()


class _Ctx:
    def __init__(self, status=0):
        self.op = _Op(status)
        self.started_on = None

    def request_purchase_async(self, store_id):
        assert store_id == msstore.ADD_ON_STORE_ID
        self.started_on = threading.current_thread()
        return self.op


@pytest.mark.parametrize("status,word", [(0, "succeeded"), (1, "already"),
                                         (2, "cancelled"), (3, "network"),
                                         (4, "server"), (99, "unknown")])
def test_the_purchase_status_is_named(status, word):
    ctx = _Ctx(status)
    assert msstore.finish_purchase(msstore.start_purchase(1, context=ctx)) == word


def test_the_owner_window_is_given_to_the_context(monkeypatch):
    given = []
    fake_store = type(sys)("winrt.windows.services.store")
    fake_store.StoreContext = type("SC", (), {"get_default": staticmethod(lambda: "CTX")})
    fake_interop = type(sys)("winrt.runtime.interop")
    fake_interop.initialize_with_window = lambda obj, hwnd: given.append((obj, hwnd))
    monkeypatch.setitem(sys.modules, "winrt.windows.services.store", fake_store)
    monkeypatch.setitem(sys.modules, "winrt.runtime.interop", fake_interop)
    assert msstore._context(4242) == "CTX"
    assert given == [("CTX", 4242)]
    given.clear()
    msstore._context()
    assert given == [], "a call that shows no dialog needs no owner"


def test_a_missing_store_module_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setitem(sys.modules, "winrt.windows.services.store", None)
    with pytest.raises(msstore.StoreUnavailable):
        msstore._context()


@pytest.mark.skipif(sys.platform != "win32", reason="the Store module is Windows-only")
def test_the_store_module_the_build_needs_is_installed():
    """The positive control against the real package, not a fake. This is the
    import the live build could never make."""
    from winrt.runtime.interop import initialize_with_window  # noqa: F401
    from winrt.windows.services.store import StoreContext  # noqa: F401


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_panel_starts_on_the_ui_thread_and_waits_off_it(qapp):
    from PySide6.QtWidgets import QApplication

    from app.ui.settings import StoreSubscribePanel

    ctx = _Ctx(0)
    windows = []

    class Store:
        offer = staticmethod(lambda: msstore.Offer("Dawnlist monthly", "£79.00"))

        @staticmethod
        def start_purchase(hwnd):
            windows.append(hwnd)
            return msstore.start_purchase(hwnd, context=ctx)

        finish_purchase = staticmethod(msstore.finish_purchase)

    panel = StoreSubscribePanel(store=Store())
    changed = []
    panel.entitlement_changed.connect(changed.append)
    deadline = time.time() + 3
    while time.time() < deadline and not panel.btn_subscribe.isEnabled():
        QApplication.processEvents()
        time.sleep(0.01)
    panel._subscribe()
    deadline = time.time() + 3
    while time.time() < deadline and not changed:
        QApplication.processEvents()
        time.sleep(0.01)
    assert ctx.started_on is threading.main_thread()
    assert ctx.op.got_on is not threading.main_thread()
    assert windows and isinstance(windows[0], int) and windows[0] != 0
    assert changed == [True]
