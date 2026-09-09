"""Selling the subscription inside a Mac App Store build.

`mac_receipt.py` READS an App Store receipt. Nothing in Dawnlist ever created
one, which meant a Mac App Store build carrying a subscription product offered
no way to buy it — the application reported that it could not confirm a
subscription and stopped there. App Review rejects that, and rightly: an app
with a subscription product must let a customer buy it in the app.

This module is the missing half. It fetches the product from Apple, starts a
payment, observes the transaction queue, and refreshes the receipt afterwards
so `mac_receipt.read_receipt()` has something to read.

WHY StoreKit 1 AND PyObjC
-------------------------
StoreKit 2 is Swift-only and cannot be reached from Python. StoreKit 1 is
deprecated-but-supported and is exposed through PyObjC, which is the only route
available to a PySide6 application. If Dawnlist is ever rewritten with a Swift
shell, this is the module that goes away.

EVERYTHING HERE DEGRADES INSTEAD OF RAISING
-------------------------------------------
Importing this module must never break a Windows build, a direct-download
build, or a development run. Off macOS, or without PyObjC, or outside a
packaged bundle, `available()` reports False and every other call is a no-op
that returns a refusal rather than throwing. That is the same discipline
`store_entitlement.py` uses in Easy-Post, and for the same reason: an import
that can explode makes the module radioactive to callers.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
IT DOES NOT DECIDE WHETHER THE SUBSCRIPTION IS VALID. It gets a receipt and
hands it on. Validation belongs to the Worker, against Apple's App Store Server
API, exactly as `mac_receipt.py` documents — a client-side check is one a
determined user patches out, and one that is subtly wrong fails open while
looking fine.

IT DOES NOT ACCEPT A LICENCE KEY, and there is no code path here that could.
Guideline 3.1.1 names licence keys; a MAS build must never take one.

!!! UNVERIFIED AGAINST A REAL MAC AS OF 2026-09-09 !!!
------------------------------------------------------
This was written on Windows. Every StoreKit call below is written from Apple's
documented API and NONE of it has been executed. A CI runner can compile it;
it cannot exercise an interactive purchase, which needs a signed build, a
sandbox tester account and somebody clicking. Treat the whole module as a
first draft until it has run on a Mac, and do not let a green CI build be
mistaken for evidence that it works — that is the exact failure this project
keeps recording.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from enum import Enum

#: The subscription created in App Store Connect on 2026-09-09, group
#: "Dawnlist". MUST match the product id there exactly; a mismatch returns an
#: empty product list, which looks identical to "the App Store is unreachable".
PRODUCT_ID = "com.spencerfields.dawnlist.monthly"

#: How long to wait for Apple before giving up, in seconds. A products request
#: on a cold network can genuinely take several seconds; a purchase involves a
#: human and must not be timed out at all.
PRODUCTS_TIMEOUT = 30.0


class Outcome(Enum):
    """What happened, as something the caller can branch on."""

    PURCHASED = "purchased"        # bought now, or an existing subscription restored
    CANCELLED = "cancelled"        # the person changed their mind; not an error
    FAILED = "failed"              # Apple refused or the network broke
    UNAVAILABLE = "unavailable"    # not a MAS build, or StoreKit is not reachable


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    #: Apple's own words where there are any. Shown to the user, so it must not
    #: be a raw exception repr.
    detail: str = ""


def available() -> bool:
    """True only where a real purchase could actually be made.

    Three conditions, and all of them are ordinary rather than exceptional:
    macOS, PyObjC present, and a `mas` build. A direct-download build on a Mac
    is deliberately excluded — it sells through Paddle and must not offer a
    StoreKit purchase as well, or a customer could pay twice.
    """
    if sys.platform != "darwin":
        return False
    try:
        import StoreKit  # noqa: F401
    except Exception:  # noqa: BLE001 - PyObjC absent or the framework is missing
        return False
    from app.core.build_variant import variant
    return variant() == "mas"


def price() -> str | None:
    """The localised price string to show on the button, or None.

    ASKED OF APPLE, NEVER HARDCODED. Apple sets the price per storefront from
    the price point chosen in App Store Connect, and it is formatted for the
    customer's region. A hardcoded "$79/month" is wrong in most of the 175
    territories the subscription is available in, and showing a price that
    differs from what the App Store then charges is a rejection.
    """
    product = _fetch_product()
    if product is None:
        return None
    try:
        from Foundation import NSNumberFormatter, NSNumberFormatterCurrencyStyle
        fmt = NSNumberFormatter.alloc().init()
        fmt.setNumberStyle_(NSNumberFormatterCurrencyStyle)
        fmt.setLocale_(product.priceLocale())
        return fmt.stringFromNumber_(product.price())
    except Exception:  # noqa: BLE001
        return None


def _fetch_product():
    """The SKProduct for PRODUCT_ID, or None.

    An empty result is NOT an error and must not be reported as one: Apple
    returns an empty product list when the id is unknown to the storefront,
    which is also what happens for a brand-new subscription that has not
    finished propagating. The caller says "not available right now", which is
    true in both cases.
    """
    if not available():
        return None
    try:
        import StoreKit
        from Foundation import NSObject, NSSet
    except Exception:  # noqa: BLE001
        return None

    done = threading.Event()
    box: dict = {}

    class _Delegate(NSObject):
        def productsRequest_didReceiveResponse_(self, request, response):
            products = list(response.products() or [])
            box["product"] = products[0] if products else None
            done.set()

        def request_didFailWithError_(self, request, error):
            box["error"] = str(error.localizedDescription()) if error else "unknown"
            done.set()

    delegate = _Delegate.alloc().init()
    request = StoreKit.SKProductsRequest.alloc().initWithProductIdentifiers_(
        NSSet.setWithArray_([PRODUCT_ID]))
    request.setDelegate_(delegate)
    request.start()

    # The delegate fires on the main run loop, which Qt is already spinning.
    if not done.wait(PRODUCTS_TIMEOUT):
        return None
    return box.get("product")


def can_make_payments() -> bool:
    """False when purchasing is disabled on the device, e.g. parental controls.

    Worth asking separately, because the button should explain rather than fail
    when the person is not allowed to buy anything at all.
    """
    if not available():
        return False
    try:
        import StoreKit
        return bool(StoreKit.SKPaymentQueue.canMakePayments())
    except Exception:  # noqa: BLE001
        return False


def purchase(on_finished) -> None:
    """Start a subscription purchase. Calls `on_finished(Result)` when done.

    ASYNCHRONOUS BY NATURE. A purchase shows Apple's own sheet and waits for a
    human, so there is no sensible timeout and no blocking version of this.
    The caller keeps its UI responsive and reacts to the callback.

    THE RECEIPT IS REFRESHED BEFORE REPORTING SUCCESS. `read_receipt()` reads a
    file that the App Store writes, and on a first purchase that file does not
    exist yet. Reporting PURCHASED without refreshing hands the caller a
    success it cannot act on, which is worse than a failure because it looks
    like the entitlement check is broken.
    """
    if not available():
        on_finished(Result(Outcome.UNAVAILABLE,
                           "This build cannot purchase from the App Store."))
        return
    if not can_make_payments():
        on_finished(Result(Outcome.UNAVAILABLE,
                           "Purchases are disabled on this Mac."))
        return

    product = _fetch_product()
    if product is None:
        on_finished(Result(Outcome.FAILED,
                           "The App Store did not return the subscription. "
                           "This is usually temporary."))
        return

    try:
        import StoreKit
        from Foundation import NSObject
    except Exception as exc:  # noqa: BLE001
        on_finished(Result(Outcome.UNAVAILABLE, str(exc)))
        return

    queue = StoreKit.SKPaymentQueue.defaultQueue()

    class _Observer(NSObject):
        def paymentQueue_updatedTransactions_(self, q, transactions):
            for t in transactions:
                state = t.transactionState()
                if state == StoreKit.SKPaymentTransactionStatePurchasing:
                    continue
                # FINISH EVERY SETTLED TRANSACTION. An unfinished one is
                # re-delivered on every launch forever, and Apple treats a
                # queue that never drains as a defect.
                if state in (StoreKit.SKPaymentTransactionStatePurchased,
                             StoreKit.SKPaymentTransactionStateRestored):
                    q.finishTransaction_(t)
                    q.removeTransactionObserver_(self)
                    refresh_receipt(lambda ok: on_finished(
                        Result(Outcome.PURCHASED) if ok else
                        Result(Outcome.FAILED,
                               "Purchased, but the receipt did not arrive. "
                               "Reopen Dawnlist in a moment.")))
                    return
                if state == StoreKit.SKPaymentTransactionStateFailed:
                    err = t.error()
                    cancelled = bool(
                        err and err.code() == StoreKit.SKErrorPaymentCancelled)
                    q.finishTransaction_(t)
                    q.removeTransactionObserver_(self)
                    on_finished(Result(
                        Outcome.CANCELLED if cancelled else Outcome.FAILED,
                        "" if cancelled else
                        (str(err.localizedDescription()) if err else "")))
                    return

    observer = _Observer.alloc().init()
    # Held on the queue, but keep a Python reference too: PyObjC will collect
    # it otherwise and the callback never fires.
    _LIVE.append(observer)
    queue.addTransactionObserver_(observer)
    payment = StoreKit.SKPayment.paymentWithProduct_(product)
    queue.addPayment_(payment)


def restore(on_finished) -> None:
    """Restore an existing subscription. Apple REQUIRES this to be offered.

    A customer who subscribed on another Mac, or who reinstalled, has paid and
    must be able to get their entitlement back without paying again. An app
    that sells a subscription and offers no restore is rejected.

    IT MUST COUNT WHAT WAS RESTORED, AND THE FIRST VERSION DID NOT.
    `paymentQueueRestoreCompletedTransactionsFinished_` fires when the RESTORE
    OPERATION finishes, which includes finishing with nothing found. The first
    version treated that callback as success and then asked only whether a
    receipt file existed — which is true for every Mac App Store app, holding
    a subscription or not, because the App Store writes one for the app
    itself.

    So pressing Restore Purchase on a Mac that had never subscribed answered
    "Subscribed. The feed reads for you from tomorrow morning." Observed on a
    real Mac, 2026-09-09, before any purchase had been attempted.

    That is the `/health` bug wearing a different hat: a question whose answer
    is yes for everybody, standing in for one that would have been no. The
    feed itself was never at risk — `build_provider` exchanges the receipt
    with the Worker and the SERVER decides — but the screen told the user
    something the server would have contradicted the next morning, by which
    time they would have no reason to connect the two.
    """
    if not available():
        on_finished(Result(Outcome.UNAVAILABLE,
                           "This build cannot restore App Store purchases."))
        return
    try:
        import StoreKit
        from Foundation import NSObject
    except Exception as exc:  # noqa: BLE001
        on_finished(Result(Outcome.UNAVAILABLE, str(exc)))
        return

    queue = StoreKit.SKPaymentQueue.defaultQueue()

    restored = []

    class _Restorer(NSObject):
        def paymentQueueRestoreCompletedTransactionsFinished_(self, q):
            q.removeTransactionObserver_(self)
            if not restored:
                # Apple found nothing on this Apple ID. That is an ANSWER, and
                # the honest one: nothing was restored, so nothing is claimed.
                on_finished(Result(Outcome.FAILED,
                                   "Nothing to restore on this account."))
                return
            # Something really was restored. Refresh the receipt so the caller
            # has one to present, and let the SERVER be the judge of whether
            # the subscription it describes is still active — a restored
            # transaction can be expired or refunded, and only the Worker's
            # exchange with Apple can tell.
            refresh_receipt(lambda ok: on_finished(
                Result(Outcome.PURCHASED) if ok else
                Result(Outcome.FAILED,
                       "Restored, but the receipt did not arrive. Reopen "
                       "Dawnlist in a moment.")))

        def paymentQueue_restoreCompletedTransactionsFailedWithError_(self, q, error):
            q.removeTransactionObserver_(self)
            on_finished(Result(Outcome.FAILED,
                               str(error.localizedDescription()) if error else ""))

        def paymentQueue_updatedTransactions_(self, q, transactions):
            for t in transactions:
                if t.transactionState() == StoreKit.SKPaymentTransactionStateRestored:
                    # COUNTED, not just finished. This is the only place that
                    # knows anything was actually found.
                    restored.append(t)
                    q.finishTransaction_(t)

    restorer = _Restorer.alloc().init()
    _LIVE.append(restorer)
    queue.addTransactionObserver_(restorer)
    queue.restoreCompletedTransactions()


def refresh_receipt(on_finished) -> None:
    """Ask the App Store to write a receipt, then report whether one exists.

    Needed because the receipt file is absent until the App Store puts it
    there, which on a first purchase is AFTER the transaction completes.
    """
    if not available():
        on_finished(False)
        return
    try:
        import StoreKit
        from Foundation import NSObject
    except Exception:  # noqa: BLE001
        on_finished(False)
        return

    from app.core.mac_receipt import read_receipt

    class _Refresh(NSObject):
        def requestDidFinish_(self, request):
            on_finished(read_receipt() is not None)

        def request_didFailWithError_(self, request, error):
            # A refresh can fail while a perfectly good receipt already sits on
            # disk, so ask the disk rather than trusting the error.
            on_finished(read_receipt() is not None)

    delegate = _Refresh.alloc().init()
    _LIVE.append(delegate)
    request = StoreKit.SKReceiptRefreshRequest.alloc().initWithReceiptProperties_(None)
    request.setDelegate_(delegate)
    request.start()


#: PyObjC delegates are referenced only weakly by the frameworks that use them,
#: so a delegate that exists solely as a local is collected before it fires and
#: the callback silently never happens. Keeping them here is not a leak worth
#: worrying about: there are at most a handful per session.
_LIVE: list = []
