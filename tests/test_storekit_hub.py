"""The Mac transaction observer's decisions, against fakes.

WHAT THIS PROVES, AND WHAT IT DOES NOT. `TransactionHub` holds every decision
the observer makes and touches no framework, so these run anywhere. They prove
the DECISIONS: which transactions are ours, when one is finished, what reaches
the screen. They prove nothing about PyObjC or StoreKit themselves — only a
signed build and a sandbox tester do that.

The defects they pin:
  * transactions were finished BEFORE the Worker confirmed anything, so an
    outage lost the only retry Apple would give;
  * a new observer was stacked on every press, each reporting the same
    transaction;
  * Restore reported success for any product, and before anything confirmed
    that the subscription was active;
  * Deferred (Ask to Buy) had no handling at all.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.core import mac_storekit
from app.core.entitlement import AppleExchange
from app.core.mac_storekit import (DEFAULT_STATES as S, PRODUCT_ID, Outcome,
                                   TransactionHub)


class Txn:
    def __init__(self, state, *, product=PRODUCT_ID, tid="2000000999",
                 original=None, error=None):
        self._state, self._product, self._tid = state, product, tid
        self._original, self._error = original, error

    def transactionState(self):
        return self._state

    def payment(self):
        return SimpleNamespace(productIdentifier=lambda: self._product)

    def transactionIdentifier(self):
        return self._tid

    def originalTransaction(self):
        return self._original

    def error(self):
        return self._error


class Queue:
    def __init__(self):
        self.finished, self.payments, self.pending = [], [], []
        self.restores = 0

    def finishTransaction_(self, t):
        self.finished.append(t)

    def addPayment_(self, payment):
        self.payments.append(payment)

    def transactions(self):
        return self.pending

    def restoreCompletedTransactions(self):
        self.restores += 1


class Runner:
    """Holds background work until the test lets it run, so a test can look at
    the queue BETWEEN delivery and the Worker's answer."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn, on_done):
        self.jobs.append((fn, on_done))

    def drain(self):
        while self.jobs:
            fn, on_done = self.jobs.pop(0)
            on_done(fn())


def make(answer=None):
    queue, runner, seen, asked = Queue(), Runner(), [], []

    def exchange(oid):
        asked.append(oid)
        return answer if answer is not None else AppleExchange(
            "licence", licence_key="DAWN-MAC")

    hub = TransactionHub(queue=queue, exchange=exchange, run=runner,
                         emit=seen.append, payment_for=lambda p: ("pay", p))
    return SimpleNamespace(hub=hub, queue=queue, run=runner, seen=seen,
                           asked=asked)


# -- a purchase ---------------------------------------------------------------

def test_a_purchase_is_finished_only_after_the_worker_confirms_it():
    t = make()
    txn = Txn(S.purchased)
    t.hub.transactions_updated(t.queue, [txn])
    assert t.queue.finished == [], "finished before anything was confirmed"
    assert t.seen == [], "success claimed before the Worker answered"

    t.run.drain()
    assert t.queue.finished == [txn]
    assert [r.outcome for r in t.seen] == [Outcome.PURCHASED]
    assert t.asked == ["2000000999"]


def test_the_original_transaction_id_is_what_the_worker_is_asked():
    t = make()
    original = Txn(S.purchased, tid="2000000111")
    t.hub.transactions_updated(t.queue, [Txn(S.purchased, tid="2000000555",
                                             original=original)])
    t.run.drain()
    assert t.asked == ["2000000111"]


def test_an_unreachable_worker_leaves_the_transaction_for_apple_to_redeliver():
    t = make(AppleExchange("unreachable"))
    t.hub.transactions_updated(t.queue, [Txn(S.purchased)])
    t.run.drain()
    assert t.queue.finished == []
    assert t.seen[0].outcome is Outcome.FAILED
    assert "charged twice" in t.seen[0].detail


def test_a_licence_the_keychain_would_not_keep_is_not_finished():
    t = make(AppleExchange("licence", licence_key="DAWN-MAC", saved=False))
    t.hub.transactions_updated(t.queue, [Txn(S.purchased)])
    t.run.drain()
    assert t.queue.finished == []
    assert t.seen[0].outcome is Outcome.FAILED


def test_a_refusal_is_finished_and_said_plainly():
    t = make(AppleExchange("refused", error="not_subscribed"))
    txn = Txn(S.purchased)
    t.hub.transactions_updated(t.queue, [txn])
    t.run.drain()
    assert t.queue.finished == [txn]
    assert t.seen[0].outcome is Outcome.FAILED
    assert "not active" in t.seen[0].detail


def test_an_exchange_that_raises_is_unconfirmed_not_a_crash():
    t = make()
    t.hub.exchange = lambda oid: (_ for _ in ()).throw(RuntimeError("boom"))
    t.hub.transactions_updated(t.queue, [Txn(S.purchased)])
    t.run.drain()
    assert t.queue.finished == []
    assert t.seen[0].outcome is Outcome.FAILED


def test_a_transaction_for_another_product_is_neither_delivered_nor_finished():
    """Finishing tells Apple its content was delivered. The purchase tests
    above are the positive control."""
    t = make()
    t.hub.transactions_updated(t.queue, [Txn(S.purchased, product="com.other.app")])
    t.run.drain()
    assert t.asked == [] and t.queue.finished == [] and t.seen == []


def test_ask_to_buy_says_it_is_waiting_and_is_never_finished():
    t = make()
    t.hub.purchase_in_flight = True
    t.hub.transactions_updated(t.queue, [Txn(S.deferred)])
    assert t.queue.finished == []
    assert t.seen[0].outcome is Outcome.DEFERRED
    assert "approval" in t.seen[0].detail
    assert not t.hub.purchase_in_flight


def test_a_cancelled_sheet_is_not_a_failure():
    t = make()
    cancelled = SimpleNamespace(code=lambda: S.cancelled,
                                localizedDescription=lambda: "Cancelled")
    txn = Txn(S.failed, error=cancelled)
    t.hub.transactions_updated(t.queue, [txn])
    assert t.seen[0].outcome is Outcome.CANCELLED
    assert t.queue.finished == [txn]


def test_a_redelivered_transaction_is_not_exchanged_twice_at_once():
    t = make()
    txn = Txn(S.purchased)
    t.hub.transactions_updated(t.queue, [txn])
    t.hub.transactions_updated(t.queue, [txn])
    t.run.drain()
    assert t.asked == ["2000000999"]


# -- pressing Subscribe more than once ----------------------------------------

def test_a_second_press_while_apples_queue_holds_the_first_adds_nothing():
    t = make()
    assert t.hub.purchase("product") is None
    t.queue.pending = [Txn(S.purchasing)]
    again = t.hub.purchase("product")
    assert again.outcome is Outcome.IN_PROGRESS
    assert len(t.queue.payments) == 1


def test_a_press_after_a_sheet_that_never_appeared_can_try_again():
    """The positive control: the flag alone would strand somebody whose
    purchase sheet never opened."""
    t = make()
    t.hub.purchase("product")
    t.queue.pending = []
    assert t.hub.purchase("product") is None
    assert len(t.queue.payments) == 2


def test_a_purchase_apple_kept_open_from_an_earlier_launch_is_said_not_repeated():
    """Build 165 on the cloud Mac, 2026-09-13: the first press was interrupted
    by sign-in and 2FA, Apple re-added that open transaction at every launch,
    and each later press called addPayment, which Apple silently discards.
    A fresh process has no flag set, so the flag cannot be what decides."""
    t = make()
    t.queue.pending = [Txn(S.purchasing)]
    said = t.hub.purchase("product")
    assert said.outcome is Outcome.IN_PROGRESS
    assert said.detail, "a wait with no end needs words, not 'talking to…'"
    assert t.queue.payments == []


def test_an_open_purchase_for_another_product_does_not_block_this_one():
    t = make()
    t.queue.pending = [Txn(S.purchasing, product="com.example.other")]
    assert t.hub.purchase("product") is None
    assert len(t.queue.payments) == 1


def test_the_launch_redelivery_of_a_purchased_transaction_still_reaches_the_worker():
    """The positive control for the check above: only purchasing and deferred
    block a press. A completed purchase Apple redelivers is exchanged."""
    t = make()
    t.hub.transactions_updated(t.queue, [Txn(S.purchased)])
    t.run.drain()
    assert t.asked == ["2000000999"]
    assert t.seen[-1].outcome is Outcome.PURCHASED


def test_every_step_is_written_to_a_file_the_terminal_can_read(tmp_path, monkeypatch):
    """Build 168 wrote no system-log line while Apple re-added a transaction,
    and nothing outside the process could say whether the observer ran."""
    log = tmp_path / "storekit.log"
    monkeypatch.setattr(mac_storekit, "trace_path", lambda: log)
    t = make()
    t.queue.pending = [Txn(S.purchasing)]
    t.hub.purchase("product")
    t.hub.transactions_updated(t.queue, [Txn(S.purchased)])
    t.run.drain()
    text = log.read_text(encoding="utf-8")
    assert f"{PRODUCT_ID}=purchasing" in text
    assert "state purchased" in text
    assert "worker answered licence" in text


def test_a_trace_file_that_cannot_be_written_never_breaks_a_purchase(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(mac_storekit, "trace_path", lambda: blocker / "storekit.log")
    t = make()
    assert t.hub.purchase("product") is None
    assert len(t.queue.payments) == 1


# -- restore ------------------------------------------------------------------

def test_restore_with_nothing_on_the_account_claims_nothing():
    t = make()
    assert t.hub.restore() is None
    t.hub.restore_finished(t.queue)
    assert t.seen[0].outcome is Outcome.FAILED
    assert "othing to restore" in t.seen[0].detail


def test_restore_of_another_product_is_nothing_to_restore():
    t = make()
    t.hub.restore()
    t.hub.transactions_updated(t.queue, [Txn(S.restored, product="com.other.app")])
    t.hub.restore_finished(t.queue)
    t.run.drain()
    assert t.seen[0].outcome is not Outcome.PURCHASED
    assert t.asked == []


def test_restore_reports_success_only_after_the_worker_confirms():
    t = make()
    txn = Txn(S.restored)
    t.hub.restore()
    t.hub.transactions_updated(t.queue, [txn])
    t.hub.restore_finished(t.queue)
    assert t.seen == [], "the restore operation finishing confirms nothing"

    t.run.drain()
    assert [r.outcome for r in t.seen] == [Outcome.PURCHASED]
    assert t.queue.finished == [txn]


def test_a_restored_subscription_that_lapsed_is_not_success():
    t = make(AppleExchange("refused", error="not_subscribed"))
    t.hub.restore()
    t.hub.transactions_updated(t.queue, [Txn(S.restored)])
    t.hub.restore_finished(t.queue)
    t.run.drain()
    assert t.seen[0].outcome is Outcome.FAILED
    assert "not active" in t.seen[0].detail


def test_a_second_restore_while_one_runs_starts_nothing():
    t = make()
    t.hub.restore()
    assert t.hub.restore().outcome is Outcome.IN_PROGRESS
    assert t.queue.restores == 1


def test_a_failed_restore_says_why_and_can_be_tried_again():
    t = make()
    t.hub.restore()
    t.hub.restore_failed(t.queue, SimpleNamespace(
        localizedDescription=lambda: "Cannot connect to iTunes Store"))
    assert "iTunes Store" in t.seen[0].detail
    assert t.hub.restore() is None


# -- the module entry points --------------------------------------------------

def test_subscribe_never_fetches_the_product_itself(monkeypatch):
    """The fetch waits for a delegate on the main run loop. Called from the
    button it waited for itself, then reported the subscription unavailable."""
    def explode():
        raise AssertionError("purchase() must use the product fetched earlier")

    monkeypatch.setattr(mac_storekit, "available", lambda: True)
    monkeypatch.setattr(mac_storekit, "can_make_payments", lambda: True)
    monkeypatch.setattr(mac_storekit, "_fetch_product", explode)
    monkeypatch.setattr(mac_storekit, "_PRODUCT", None)
    hub = make().hub
    monkeypatch.setattr(mac_storekit, "_HUB", hub)

    assert mac_storekit.purchase().outcome is Outcome.FAILED

    monkeypatch.setattr(mac_storekit, "_PRODUCT", "kept-product")
    assert mac_storekit.purchase() is None
    assert hub.queue.payments == [("pay", "kept-product")]


def test_loading_the_product_on_the_main_thread_fetches_nothing(monkeypatch):
    def explode():
        raise AssertionError("would wait for itself")

    monkeypatch.setattr(mac_storekit, "_fetch_product", explode)
    monkeypatch.setattr(mac_storekit, "_PRODUCT", None)
    assert threading.current_thread() is threading.main_thread()
    assert mac_storekit.load_product() is None


def test_loading_the_product_off_the_main_thread_keeps_it(monkeypatch):
    """The positive control."""
    monkeypatch.setattr(mac_storekit, "_fetch_product", lambda: "fetched")
    monkeypatch.setattr(mac_storekit, "_PRODUCT", None)
    box = []
    worker = threading.Thread(target=lambda: box.append(mac_storekit.load_product()))
    worker.start()
    worker.join()
    assert box == ["fetched"]
    assert mac_storekit.has_product()


def _fake_frameworks(monkeypatch):
    import sys
    import types

    queue = Queue()
    queue.observers = []
    queue.addTransactionObserver_ = queue.observers.append
    storekit = types.ModuleType("StoreKit")
    storekit.SKPaymentQueue = SimpleNamespace(defaultQueue=lambda: queue)
    storekit.SKPayment = SimpleNamespace(paymentWithProduct_=lambda p: p)

    class Observer:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

    objc = types.ModuleType("app.core.mac_storekit_objc")
    objc.DawnlistTransactionObserver = Observer
    monkeypatch.setitem(sys.modules, "StoreKit", storekit)
    monkeypatch.setitem(sys.modules, "app.core.mac_storekit_objc", objc)
    monkeypatch.setattr(mac_storekit, "available", lambda: True)
    monkeypatch.setattr(mac_storekit, "_HUB", None)
    monkeypatch.setattr(mac_storekit, "_LIVE", [])
    return queue


def test_launch_registers_exactly_one_observer_however_often_it_is_asked(monkeypatch):
    queue = _fake_frameworks(monkeypatch)
    for _ in range(3):
        assert mac_storekit.install(exchange=lambda oid: None,
                                    run=lambda fn, done: None, emit=print)
    assert len(queue.observers) == 1


def test_nothing_is_registered_where_storekit_is_not_available(monkeypatch):
    queue = _fake_frameworks(monkeypatch)
    monkeypatch.setattr(mac_storekit, "available", lambda: False)
    assert mac_storekit.install(exchange=None, run=None, emit=None) is False
    assert queue.observers == []


@pytest.mark.parametrize("build,registered", [("mas", True), ("store", False),
                                              ("direct", False)])
def test_the_application_starts_the_observer_only_on_a_mac_build(monkeypatch, build,
                                                                  registered):
    from app import main

    calls = []
    monkeypatch.setattr("app.core.build_variant.variant", lambda: build)
    monkeypatch.setattr(mac_storekit, "install",
                        lambda **kw: calls.append(kw) or True)
    main.start_storekit()
    assert bool(calls) is registered
    if registered:
        from app.core.entitlement import exchange_and_cache
        assert calls[0]["exchange"] is exchange_and_cache


def test_both_launch_paths_start_the_observer():
    """Asserted on the source: running the Qt application is not a unit test,
    and an observer registered only behind Settings misses every transaction
    Apple delivers at launch."""
    import ast
    import inspect

    from app import main

    for fn in (main._launch_ui, main._launch_onboarding):
        tree = ast.parse(inspect.getsource(fn))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "start_storekit" in called, f"{fn.__name__} never starts it"
