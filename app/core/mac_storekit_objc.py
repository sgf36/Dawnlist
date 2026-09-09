"""The four Objective-C classes StoreKit needs, defined ONCE per process.

WHY THEY LIVE IN THEIR OWN MODULE
---------------------------------
Objective-C's class registry is **global and per-process**. A class defined
inside a function is registered on the first call and raises on every call
after it, for the life of the process:

    objc.error: _Delegate is overriding existing Objective-C class

All four were declared inside the functions that used them, which was the
natural way to write it in Python — the callbacks want the caller's
`on_finished`, its `threading.Event`, its result box — and it is the one thing
PyObjC will not allow.

WHAT IT COST. Diagnosed on the cloud Mac against build 72 by running the
shipped bytecode against the app's own bundled CPython and PyObjC. The daemon
log settles it — four launches in an evening, exactly one product fetch per
process, and `addPayment` **zero times**:

    4  Requesting Media API product batch    <- one per launch, from price()
    1  Restoring completed legacy transactions
    0  addPayment                            <- never, not once

**No purchase had ever reached StoreKit.** Not a failed one, not a cancelled
one. And it looked like an App Store fault rather than a bug, because
`price()` runs once when the panel is built: the FIRST `_fetch_product()`
succeeds, the price renders, the screen looks healthy. Pressing Subscribe is
the *second* call, which raises on a worker thread where nothing is catching
it, so the callback never comes and the panel sits on "Talking to the App
Store…".

A MODULE, NOT A CACHED FACTORY. A factory that builds the class once and
memoises it would also register only once — and would leave the class defined
inside a function, which is the shape `tests/test_objc_classes.py` refuses. A
module is imported once by Python's own caching, so the guard can be absolute
with no exception to remember. This file is imported only from macOS paths,
which is why the `Foundation` import at the top is safe.

STATE LIVES ON THE INSTANCE. PyObjC subclasses take ordinary Python
attributes, so everything the closures used to capture is assigned to the
delegate before the request starts.

NAMES ARE PREFIXED DELIBERATELY. These are global to the Objective-C runtime
in a process that also loads PySide6, PIL and anthropic. `_Delegate` and
`_Observer` are exactly the names something else would pick.
"""
from __future__ import annotations

import StoreKit
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


class DawnlistPaymentObserver(NSObject):
    """Watches one purchase through. Owns `on_finished` and `refresh_receipt`."""

    def paymentQueue_updatedTransactions_(self, q, transactions):
        for t in transactions:
            state = t.transactionState()
            if state == StoreKit.SKPaymentTransactionStatePurchasing:
                continue
            # FINISH EVERY SETTLED TRANSACTION. An unfinished one is
            # re-delivered on every launch forever, and Apple treats a queue
            # that never drains as a defect.
            if state in (StoreKit.SKPaymentTransactionStatePurchased,
                         StoreKit.SKPaymentTransactionStateRestored):
                q.finishTransaction_(t)
                q.removeTransactionObserver_(self)
                done = self.on_finished
                self.refresh_receipt(lambda ok: done(
                    self.purchased() if ok else self.no_receipt()))
                return
            if state == StoreKit.SKPaymentTransactionStateFailed:
                err = t.error()
                cancelled = bool(
                    err and err.code() == StoreKit.SKErrorPaymentCancelled)
                q.finishTransaction_(t)
                q.removeTransactionObserver_(self)
                self.on_finished(self.failed(cancelled, err))
                return


class DawnlistRestoreObserver(NSObject):
    """Restores. Owns `on_finished` and `restored`, the list it counts into."""

    def paymentQueueRestoreCompletedTransactionsFinished_(self, q):
        q.removeTransactionObserver_(self)
        if not self.restored:
            # Apple found nothing on this Apple ID. That is an ANSWER, and the
            # honest one: nothing was restored, so nothing is claimed.
            self.on_finished(self.nothing_to_restore())
            return
        done = self.on_finished
        self.refresh_receipt(lambda ok: done(
            self.purchased() if ok else self.no_receipt()))

    def paymentQueue_restoreCompletedTransactionsFailedWithError_(self, q, error):
        q.removeTransactionObserver_(self)
        self.on_finished(self.failed(error))

    def paymentQueue_updatedTransactions_(self, q, transactions):
        for t in transactions:
            if t.transactionState() == StoreKit.SKPaymentTransactionStateRestored:
                # COUNTED, not just finished. This is the only place that
                # knows anything was actually found.
                self.restored.append(t)
                q.finishTransaction_(t)


class DawnlistReceiptDelegate(NSObject):
    """Answers `SKReceiptRefreshRequest`. Owns `on_finished`, `read_receipt`."""

    def requestDidFinish_(self, request):
        self.on_finished(self.read_receipt() is not None)

    def request_didFailWithError_(self, request, error):
        # A refresh can fail while a perfectly good receipt already sits on
        # disk, so ask the disk rather than trusting the error.
        self.on_finished(self.read_receipt() is not None)
