"""The Objective-C classes StoreKit needs, defined ONCE per process.

WHY THEY LIVE IN THEIR OWN MODULE
---------------------------------
Objective-C's class registry is **global and per-process**. A class defined
inside a function is registered on the first call and raises on every call
after it, for the life of the process:

    objc.error: _Delegate is overriding existing Objective-C class

All of them were declared inside the functions that used them, which was the
natural way to write it in Python, and it is the one thing PyObjC will not
allow.

WHAT IT COST. Diagnosed on the cloud Mac against build 72 by running the
shipped bytecode against the app's own bundled CPython and PyObjC. The daemon
log settles it — four launches in an evening, exactly one product fetch per
process, and `addPayment` **zero times**:

    4  Requesting Media API product batch    <- one per launch, from price()
    1  Restoring completed legacy transactions
    0  addPayment                            <- never, not once

**No purchase had ever reached StoreKit.**

TWO CLASSES, WHERE THERE WERE FOUR. One observer per purchase and one per
restore stacked a new observer on every press, each finishing and reporting
the same transaction. There is now one observer for the life of the process,
and it forwards everything to `mac_storekit.TransactionHub`, which holds every
decision in plain Python. The receipt-refresh delegate went with the receipt:
the Worker confirms the transaction id with Apple, so nothing waits for a
receipt file any more.

A MODULE, NOT A CACHED FACTORY. A module is imported once by Python's own
caching, so the guard can be absolute. This file is imported only from macOS
paths, which is why the `Foundation` import at the top is safe.

NAMES ARE PREFIXED DELIBERATELY. These are global to the Objective-C runtime
in a process that also loads PySide6, PIL and anthropic.
"""
from __future__ import annotations

from Foundation import NSObject


class DawnlistProductsDelegate(NSObject):
    """Answers `SKProductsRequest`. Owns `done` and `box`, set by the caller."""

    def productsRequest_didReceiveResponse_(self, request, response):
        products = list(response.products() or [])
        self.box["product"] = products[0] if products else None
        self.done.set()

    def request_didFailWithError_(self, request, error):
        self.box["error"] = str(error.localizedDescription()) if error else "unknown"
        self.done.set()


class DawnlistTransactionObserver(NSObject):
    """The one transaction observer. Owns `hub`, and decides nothing itself."""

    def paymentQueue_updatedTransactions_(self, queue, transactions):
        import threading

        from app.core.mac_storekit import _trace
        _trace(f"observer called: updatedTransactions x{len(transactions or [])} "
               f"main_thread={threading.current_thread() is threading.main_thread()}")
        self.hub.transactions_updated(queue, list(transactions or []))

    def paymentQueueRestoreCompletedTransactionsFinished_(self, queue):
        self.hub.restore_finished(queue)

    def paymentQueue_restoreCompletedTransactionsFailedWithError_(self, queue, error):
        self.hub.restore_failed(queue, error)
