"""Selling the subscription inside a Mac App Store build.

It fetches the product from Apple, starts a payment, and runs the ONE
transaction observer the process registers at launch.

HOW A PURCHASE BECOMES A LICENCE
--------------------------------
1. The panel's `refresh` calls `price()` on a worker thread, which fetches the
   SKProduct and KEEPS it.
2. Subscribe calls `purchase()`, which hands that kept product to
   `addPayment`. It never fetches: fetching on the UI thread waited for a
   delegate that fires on that same thread, so the button froze for thirty
   seconds and then reported the subscription unavailable.
3. The observer registered at launch sees the transaction, ignores anything
   that is not PRODUCT_ID, and asks the Worker (`entitlement.exchange_and_cache`)
   OFF the UI thread, with the StoreKit original transaction id.
4. Only once the Worker has given a definite answer, and that answer is kept,
   is the transaction FINISHED. A finished transaction is never delivered
   again, so finishing first threw away the only retry an outage would get.
5. The outcome reaches the screen through a Qt signal rather than a callback
   held by whichever panel started it: the panel may have closed, and a
   transaction redelivered at launch has no panel at all.

WHY StoreKit 1 AND PyObjC
-------------------------
StoreKit 2 is Swift-only and cannot be reached from Python. StoreKit 1 is
deprecated-but-supported and is exposed through PyObjC, which is the only route
available to a PySide6 application. If Dawnlist is ever rewritten with a Swift
shell, this is the module that goes away.

EVERYTHING HERE DEGRADES INSTEAD OF RAISING
-------------------------------------------
Importing this module must never break a Windows build, a direct-download
build, or a development run. Off macOS, or without PyObjC, `available()`
reports False and every other call returns a refusal rather than throwing.
`TransactionHub` itself touches no framework, so the whole flow is testable
with plain fakes.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
IT DOES NOT DECIDE WHETHER THE SUBSCRIPTION IS VALID. The Worker asks Apple
and decides; a client-side check is one a determined user patches out.

IT DOES NOT ACCEPT A LICENCE KEY, and there is no code path here that could.
Guideline 3.1.1 names licence keys; a MAS build must never take one.

!!! UNVERIFIED AGAINST A REAL MAC !!!
-------------------------------------
Rewritten on Windows on 2026-09-11 from Apple's documented API. No StoreKit
call below has executed. The tests prove the flow's decisions against fakes;
only a signed build, a sandbox tester and somebody clicking prove the calls.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace

from app.i18n import tr

#: The subscription created in App Store Connect on 2026-09-09, group
#: "Dawnlist". MUST match the product id there exactly; a mismatch returns an
#: empty product list, which looks identical to "the App Store is unreachable".
PRODUCT_ID = "com.spencerfields.dawnlist.monthly"

#: How long to wait for Apple before giving up, in seconds. A products request
#: on a cold network can genuinely take several seconds; a purchase involves a
#: human and must not be timed out at all.
PRODUCTS_TIMEOUT = 30.0

#: Apple's documented values for SKPaymentTransactionState and
#: SKErrorPaymentCancelled. Read from StoreKit at install where it exists;
#: these stand in for it in tests and must match Apple's enum.
DEFAULT_STATES = SimpleNamespace(purchasing=0, purchased=1, failed=2,
                                 restored=3, deferred=4, cancelled=2)


class Outcome(Enum):
    """What happened, as something the caller can branch on."""

    PURCHASED = "purchased"        # confirmed by the Worker, bought or restored
    CANCELLED = "cancelled"        # the person changed their mind; not an error
    FAILED = "failed"              # Apple refused, or it could not be confirmed
    UNAVAILABLE = "unavailable"    # not a MAS build, or StoreKit is not reachable
    DEFERRED = "deferred"          # waiting for someone else's approval
    IN_PROGRESS = "in_progress"    # a purchase or restore is already under way


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    #: Words for the user. Never a raw exception repr.
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


# ---------------------------------------------------------------------------
# The product
# ---------------------------------------------------------------------------

#: The SKProduct from the last successful fetch. `purchase()` uses this and
#: nothing else, so a press of Subscribe can never wait on the network.
_PRODUCT = None


def has_product() -> bool:
    return _PRODUCT is not None


def load_product():
    """Fetch the product and keep it. FOR A WORKER THREAD.

    The products delegate fires on the main run loop. Waiting for it on the
    main thread waits for itself, so there the kept product is returned and
    nothing is fetched.
    """
    global _PRODUCT
    if threading.current_thread() is threading.main_thread():
        return _PRODUCT
    product = _fetch_product()
    if product is not None:
        _PRODUCT = product
    return _PRODUCT


def price() -> str | None:
    """The localised price string to show on the button, or None.

    ASKED OF APPLE, NEVER HARDCODED. Apple sets the price per storefront from
    the price point chosen in App Store Connect, and it is formatted for the
    customer's region. A hardcoded "$79/month" is wrong in most of the 175
    territories the subscription is available in, and showing a price that
    differs from what the App Store then charges is a rejection.
    """
    product = load_product()
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
        from Foundation import NSSet

        from app.core.mac_storekit_objc import DawnlistProductsDelegate
    except Exception:  # noqa: BLE001
        return None

    delegate = DawnlistProductsDelegate.alloc().init()
    delegate.done = threading.Event()
    delegate.box = {}
    # Held across the request: PyObjC frameworks reference a delegate weakly,
    # so one that exists only as a local is collected before it fires.
    _LIVE.append(delegate)
    try:
        request = StoreKit.SKProductsRequest.alloc().initWithProductIdentifiers_(
            NSSet.setWithArray_([PRODUCT_ID]))
        request.setDelegate_(delegate)
        request.start()
        if not delegate.done.wait(PRODUCTS_TIMEOUT):
            return None
        return delegate.box.get("product")
    finally:
        _LIVE.remove(delegate)


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


# ---------------------------------------------------------------------------
# The one observer
# ---------------------------------------------------------------------------

def _product_of(transaction) -> str:
    try:
        return str(transaction.payment().productIdentifier())
    except Exception:  # noqa: BLE001
        return ""


def _original_id(transaction) -> str:
    original = transaction.originalTransaction()
    return str((original if original is not None else transaction)
               .transactionIdentifier())


def _describe(error) -> str:
    try:
        return str(error.localizedDescription()) if error is not None else ""
    except Exception:  # noqa: BLE001
        return ""


class TransactionHub:
    """Everything the observer decides, with no framework in it.

    One instance for the life of the process. Registering an observer per
    purchase stacked them: every press added another, and each one finished
    and reported the same transaction.
    """

    def __init__(self, *, queue, exchange, run, emit, payment_for,
                 states=DEFAULT_STATES):
        self.queue = queue
        #: original transaction id -> `entitlement.AppleExchange`. Network.
        self.exchange = exchange
        #: run(fn, on_done): `fn` off the UI thread, `on_done` back on it.
        self.run = run
        #: Result -> None. The Qt signal, in the application.
        self.emit = emit
        self.payment_for = payment_for
        self.states = states
        self.purchase_in_flight = False
        #: None, or the tally of a restore in progress.
        self.restoring: dict | None = None
        #: Ids being exchanged now, so a redelivery does not ask twice.
        self.exchanging: set[str] = set()

    # -- starting ---------------------------------------------------------
    def purchase(self, product) -> Result | None:
        """Add a payment, unless one of ours is still in Apple's queue."""
        if self.purchase_in_flight and self._pending_purchase():
            return Result(Outcome.IN_PROGRESS)
        # The flag alone would strand somebody whose sheet never appeared:
        # once Apple's queue holds nothing of ours, the old attempt is gone.
        self.purchase_in_flight = True
        self.queue.addPayment_(self.payment_for(product))
        return None

    def _pending_purchase(self) -> bool:
        s = self.states
        for t in list(self.queue.transactions() or []):
            if (_product_of(t) == PRODUCT_ID
                    and t.transactionState() in (s.purchasing, s.deferred)):
                return True
        return False

    def restore(self) -> Result | None:
        if self.restoring is not None:
            return Result(Outcome.IN_PROGRESS)
        self.restoring = {"pending": 0, "licensed": 0, "refused": 0,
                          "unconfirmed": 0, "finished": False}
        self.queue.restoreCompletedTransactions()
        return None

    # -- what Apple delivers ----------------------------------------------
    def transactions_updated(self, queue, transactions) -> None:
        s = self.states
        for t in list(transactions or []):
            try:
                # NOT OURS: neither delivered nor finished here. Finishing a
                # transaction tells Apple its content was delivered.
                if _product_of(t) != PRODUCT_ID:
                    continue
                state = t.transactionState()
                if state == s.purchasing:
                    continue
                if state == s.deferred:
                    # Never finished: Apple completes a deferred purchase later
                    # and delivers it again as purchased.
                    self.purchase_in_flight = False
                    self.emit(Result(Outcome.DEFERRED,
                                     tr("settings.subscribe_deferred")))
                    continue
                if state == s.failed:
                    self.purchase_in_flight = False
                    queue.finishTransaction_(t)
                    error = t.error()
                    cancelled = bool(error is not None
                                     and error.code() == s.cancelled)
                    self.emit(Result(Outcome.CANCELLED) if cancelled
                              else Result(Outcome.FAILED, _describe(error)))
                    continue
                if state in (s.purchased, s.restored):
                    self._deliver(queue, t, restored=state == s.restored)
            except Exception:  # noqa: BLE001 - a raise inside a StoreKit
                # callback is logged by PyObjC and the screen waits for ever.
                self.purchase_in_flight = False
                self.emit(Result(Outcome.FAILED, tr("storekit.unconfirmed")))

    def restore_finished(self, queue) -> None:
        if self.restoring is not None:
            self.restoring["finished"] = True
            self._report_restore()

    def restore_failed(self, queue, error) -> None:
        self.restoring = None
        self.emit(Result(Outcome.FAILED, _describe(error)))

    # -- asking the Worker ------------------------------------------------
    def _deliver(self, queue, transaction, *, restored: bool) -> None:
        counting = restored and self.restoring is not None
        if counting:
            self.restoring["pending"] += 1
        oid = _original_id(transaction)
        if oid in self.exchanging:
            if counting:
                self.restoring["pending"] -= 1
            return
        self.exchanging.add(oid)

        def ask():
            try:
                return self.exchange(oid)
            except Exception:  # noqa: BLE001 - could not ask is not a refusal
                from app.core.entitlement import AppleExchange
                return AppleExchange("unreachable")

        def answered(result):
            self.exchanging.discard(oid)
            self._answered(queue, transaction, result, counting)

        self.run(ask, answered)

    def _answered(self, queue, transaction, result, counting: bool) -> None:
        definite = result.outcome in ("licence", "refused")
        # FINISHED ONLY ON A DEFINITE, KEPT ANSWER. Unfinished, Apple delivers
        # the transaction again at the next launch, which is the retry an
        # outage or a locked Keychain needs. A refusal is finished too: Apple
        # still holds the purchase, and Restore brings it back if it renews.
        if definite and result.saved:
            queue.finishTransaction_(transaction)

        if counting and self.restoring is not None:
            self.restoring["pending"] -= 1
            if result.outcome == "licence" and result.saved:
                self.restoring["licensed"] += 1
            elif result.outcome == "refused":
                self.restoring["refused"] += 1
            else:
                self.restoring["unconfirmed"] += 1
            self._report_restore()
            return

        self.purchase_in_flight = False
        if result.outcome == "licence":
            self.emit(Result(Outcome.PURCHASED) if result.saved
                      else Result(Outcome.FAILED, tr("storekit.not_saved")))
        elif result.outcome == "refused":
            self.emit(Result(Outcome.FAILED, tr("entitlement.mac_lapsed")))
        else:
            self.emit(Result(Outcome.FAILED, tr("storekit.unconfirmed")))

    def _report_restore(self) -> None:
        tally = self.restoring
        if tally is None or not tally["finished"] or tally["pending"]:
            return
        self.restoring = None
        # SUCCESS ONLY WHEN THE WORKER CONFIRMED IT. The restore operation
        # finishing says nothing, and a receipt existing says nothing either —
        # which is how pressing Restore on a Mac that had never subscribed
        # once answered "Subscribed".
        if tally["licensed"]:
            self.emit(Result(Outcome.PURCHASED))
        elif tally["refused"]:
            self.emit(Result(Outcome.FAILED, tr("entitlement.mac_lapsed")))
        elif tally["unconfirmed"]:
            self.emit(Result(Outcome.FAILED, tr("storekit.unconfirmed")))
        else:
            self.emit(Result(Outcome.FAILED, tr("storekit.nothing_to_restore")))


_HUB: TransactionHub | None = None


def _states(storekit) -> SimpleNamespace:
    d = DEFAULT_STATES
    return SimpleNamespace(
        purchasing=getattr(storekit, "SKPaymentTransactionStatePurchasing", d.purchasing),
        purchased=getattr(storekit, "SKPaymentTransactionStatePurchased", d.purchased),
        failed=getattr(storekit, "SKPaymentTransactionStateFailed", d.failed),
        restored=getattr(storekit, "SKPaymentTransactionStateRestored", d.restored),
        deferred=getattr(storekit, "SKPaymentTransactionStateDeferred", d.deferred),
        cancelled=getattr(storekit, "SKErrorPaymentCancelled", d.cancelled))


def install(*, exchange, run, emit) -> bool:
    """Register the one transaction observer. At launch, on a `mas` build.

    AT LAUNCH, because Apple delivers unfinished transactions — a renewal, a
    purchase interrupted by a crash, an approved Ask to Buy — as soon as an
    observer exists, and an app with none loses them. A second call replaces
    the callbacks and registers nothing.
    """
    global _HUB
    if not available():
        return False
    if _HUB is not None:
        _HUB.exchange, _HUB.run, _HUB.emit = exchange, run, emit
        return True
    try:
        import StoreKit

        from app.core.mac_storekit_objc import DawnlistTransactionObserver
    except Exception:  # noqa: BLE001
        return False

    hub = TransactionHub(queue=StoreKit.SKPaymentQueue.defaultQueue(),
                         exchange=exchange, run=run, emit=emit,
                         payment_for=StoreKit.SKPayment.paymentWithProduct_,
                         states=_states(StoreKit))
    observer = DawnlistTransactionObserver.alloc().init()
    observer.hub = hub
    _LIVE.append(observer)
    hub.queue.addTransactionObserver_(observer)
    _HUB = hub
    return True


def purchase() -> Result | None:
    """Start a purchase. None when started — the answer arrives through the
    emitter given to `install` — or a Result when it could not start."""
    if not available():
        return Result(Outcome.UNAVAILABLE,
                      "This build cannot purchase from the App Store.")
    if _HUB is None:
        return Result(Outcome.UNAVAILABLE, tr("storekit.not_ready"))
    if not can_make_payments():
        return Result(Outcome.UNAVAILABLE, "Purchases are disabled on this Mac.")
    if _PRODUCT is None:
        return Result(Outcome.FAILED,
                      "The App Store did not return the subscription. "
                      "This is usually temporary.")
    return _HUB.purchase(_PRODUCT)


def restore() -> Result | None:
    """Restore an existing subscription. Apple REQUIRES this to be offered.

    A customer who subscribed on another Mac, or who reinstalled, has paid and
    must be able to get their entitlement back without paying again.
    """
    if not available():
        return Result(Outcome.UNAVAILABLE,
                      "This build cannot restore App Store purchases.")
    if _HUB is None:
        return Result(Outcome.UNAVAILABLE, tr("storekit.not_ready"))
    return _HUB.restore()


#: PyObjC delegates are referenced only weakly by the frameworks that use them,
#: so one that exists solely as a local is collected before it fires and the
#: callback silently never happens. The observer lives here for the process;
#: product delegates leave as soon as their request answers.
_LIVE: list = []
