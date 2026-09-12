"""The Mac App Store purchase path — the one that had never run.

WHAT THIS EXISTS TO STOP HAPPENING AGAIN
----------------------------------------
`app/core/mac_storekit.py` imports StoreKit and Foundation through PyObjC and
degrades to `available() == False` when that import fails — deliberately, so a
Windows or source run is a harmless no-op.

PyObjC was never a declared dependency. So the import always failed, including
inside the signed Mac App Store package. Every MAS build ever made shipped with
a permanently disabled Subscribe button and "not available right now"
underneath it, which reads as an App Store hiccup rather than a missing
library. The build's only complaint was two lines inside PyInstaller's own
output:

    ERROR: Hidden import 'CoreFoundation' not found
    ERROR: Hidden import 'objc' not found

after which it succeeded, signed, uploaded, processed and reached TestFlight.

`build_exe.spec` had listed those hidden imports since the beginning. Asking
for a module is not the same as installing one.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_pyobjc_is_a_declared_dependency():
    """Without it a MAS build cannot take a payment, and says so in a way
    nobody reads."""
    assert re.search(r"^pyobjc-framework-StoreKit\b", REQUIREMENTS, re.M), (
        "PyObjC is not in requirements.txt — `import StoreKit` will fail in "
        "the packaged app and every Subscribe button in it will be dead")


def test_pyobjc_is_scoped_to_macos():
    """A Windows install must not pull an Objective-C bridge it cannot use."""
    for line in REQUIREMENTS.splitlines():
        if line.startswith("pyobjc"):
            assert 'sys_platform == "darwin"' in line, (
                f"{line!r} would be installed on Windows too")


def test_the_spec_still_asks_for_the_frameworks():
    """The spec and the requirement have to agree: collecting a module nobody
    installed is what produced the shipped package."""
    spec = (ROOT / "packaging" / "build_exe.spec").read_text(encoding="utf-8")
    for module in ("StoreKit", "Foundation", "CoreFoundation", "objc"):
        assert f'"{module}"' in spec, f"{module} is not a hidden import"


def test_the_mac_build_refuses_a_package_that_cannot_charge(tmp_path):
    """A guard that warns is not a guard. This one has to stop the build."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_macos", ROOT / "packaging" / "build_macos.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    empty = tmp_path / "Dawnlist.app"
    (empty / "Contents" / "MacOS").mkdir(parents=True)
    with pytest.raises(SystemExit):
        module.require_storekit(empty)

    # And it passes once the framework is actually in the bundle.
    stored = empty / "Contents" / "Resources" / "StoreKit.framework"
    stored.mkdir(parents=True)
    module.require_storekit(empty)          # must not raise


# -- the panel must not stop the app while it asks Apple --------------------

class _SlowStoreKit:
    """Stands in for a StoreKit round trip, and records when it was asked."""

    def __init__(self):
        self.asked = False

    def available(self):
        return True

    def can_make_payments(self):
        return True

    def price(self):
        self.asked = True
        return "$79.00"


def test_the_price_is_not_fetched_on_the_ui_thread(qapp_and_settle):
    """`refresh()` runs on construction, and the wizard now builds this panel
    at launch. A StoreKit round trip there would hold the very first screen of
    the app before it painted — the freeze `run_in_background` exists for."""
    _qapp, settle = qapp_and_settle
    from app.ui.settings import SubscribePanel

    sk = _SlowStoreKit()
    panel = SubscribePanel(storekit=sk)
    # Constructed and returned. Whether the answer has arrived yet is the
    # point: the constructor must not have waited for it.
    assert panel.price.text() == ""

    settle(lambda: panel.price.text() == "$79.00", what="the price")
    assert sk.asked
    assert panel.buy.isEnabled()
    panel.close()


def test_an_unreachable_store_is_not_an_error_screen(qapp_and_settle):
    """Apple returns nothing for a product its storefront does not know yet,
    which is also what a brand-new subscription looks like while it
    propagates. "Not available right now" is true in both cases."""
    _qapp, settle = qapp_and_settle
    from app.ui.settings import SubscribePanel

    class Broken:
        def available(self):
            raise RuntimeError("no network")

        def can_make_payments(self):
            return False

        def price(self):
            return None

    panel = SubscribePanel(storekit=Broken())
    settle(lambda: not panel.buy.isEnabled() and panel.result.text() != "",
           what="the unavailable state")
    said = panel.result.text().lower()
    assert "right now" in said, said
    # Never "something went wrong": the app cannot tell an outage from a
    # subscription Apple has not finished propagating, and must not claim to.
    assert "error" not in said and "wrong" not in said and "fail" not in said
    panel.close()


@pytest.fixture()
def qapp_and_settle(settle):
    from PySide6.QtWidgets import QApplication
    yield (QApplication.instance() or QApplication([])), settle


# -- the receipt exchange, which nothing had ever executed -------------------
#
# `audit_seams.py` reported it as a network path no test names, and it had no
# seam to inject through — so the FIRST real execution of the whole Mac
# purchase path would have been a customer paying money.

import base64  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import urllib.error  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _opener(payload, capture=None):
    def open_it(request, timeout=None):
        if capture is not None:
            capture.append(request)
        return _Response(json.dumps(payload).encode())
    return open_it


# The receipt is now the FALLBACK shape: sent only when no transaction id was
# ever kept, for a customer who subscribed through the build in review. The
# transaction-id contract is pinned in tests/test_mac_entitlement.py.

def test_a_valid_receipt_becomes_a_licence():
    from app.core.entitlement import exchange_apple

    seen = []
    got = exchange_apple(receipt=b"receipt-bytes",
                         opener=_opener({"licence_key": "DAWN-MAC"}, seen))
    assert got.outcome == "licence" and got.licence_key == "DAWN-MAC"
    sent = json.loads(seen[0].data.decode())
    assert base64.b64decode(sent["receipt"]) == b"receipt-bytes", (
        "the receipt goes as opaque bytes — the app forms no view of what is "
        "inside it, because a client-side check is one a user patches out")


def test_the_receipt_request_identifies_the_client():
    """The same Cloudflare 1010 that refused every other Worker call. This one
    is the Mac purchase path, so a 403 here means a paid subscription that
    reaches no feed."""
    from app.core.entitlement import exchange_apple
    from app.core.http import USER_AGENT

    seen = []
    exchange_apple(receipt=b"x", opener=_opener({"licence_key": "k"}, seen))
    assert seen[0].get_header("User-agent") == USER_AGENT


def test_no_active_subscription_is_an_answer_not_an_error():
    """Lapsed, refunded, or a sandbox receipt against production. All
    legitimate, none of them a failure to hide — and since the route exists,
    Apple's answer arrives as the Worker's refusal, not as an empty reply."""
    from app.core.entitlement import exchange_apple

    def refused(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 403, "no", {},
            io.BytesIO(json.dumps({"error": "not_subscribed",
                                   "status": "expired"}).encode()))

    got = exchange_apple(receipt=b"x", opener=refused)
    assert got.outcome == "refused" and got.status == "expired"


def test_an_outage_is_not_a_refusal():
    from app.core.entitlement import exchange_apple

    def boom(_request, timeout=None):
        raise urllib.error.URLError("no network")

    assert exchange_apple(receipt=b"x", opener=boom).outcome == "unreachable"


# -- the purchase screen must never become a dead end -----------------------
#
# From a real Mac, build 66: both buttons greyed out and "Talking to the App
# Store…" underneath, for ever. Every route out of that state ran through a
# StoreKit callback, and when the callback does not arrive — no sandbox
# account signed in, Apple's sheet opening behind the window, a screen-shared
# session where it never appears — the two controls that could fix it were the
# ones that had been disabled.

class _NeverAnswers:
    """StoreKit that accepts a purchase and then says nothing, ever."""

    def __init__(self):
        self.purchases = 0

    def available(self):
        return True

    def can_make_payments(self):
        return True

    def price(self):
        return "$79.00"

    def has_product(self):
        return True

    def load_product(self):
        return "product"

    def purchase(self):
        self.purchases += 1
        return None             # started; the signal that never comes

    def restore(self):
        return None


def test_a_purchase_that_never_answers_still_leaves_a_way_out(qapp_and_settle):
    _qapp, settle = qapp_and_settle
    from app.ui.settings import SubscribePanel

    panel = SubscribePanel(storekit=_NeverAnswers())
    settle(lambda: panel.buy.isEnabled(), what="the price")

    panel.STILL_WAITING_MS = 50          # the wait, not the behaviour
    panel.buy.click()
    assert not panel.buy.isEnabled(), "busy while the App Store is asked"

    settle(lambda: panel.buy.isEnabled(), what="the still-waiting message")
    assert panel.restore.isEnabled(), (
        "Restore is the answer for somebody who already paid and is watching "
        "a sheet that never opened")
    said = panel.result.text().lower()
    assert "sandbox" in said, "say what to actually check"
    # Not a failure claim: the purchase may still be in flight.
    assert "failed" not in said and "error" not in said
    panel.close()


# -- the app opens in the machine's language --------------------------------

def test_the_system_language_is_used_when_nothing_has_been_chosen(monkeypatch):
    """Fifty catalogues shipped and every install opened in English, because
    the launch path read a setting nothing ever wrote."""
    import locale as _locale

    from app import i18n

    monkeypatch.setattr(_locale, "getlocale", lambda *a: ("fr_FR", "UTF-8"))
    assert i18n.system_locale() == "fr"


def test_a_language_with_no_catalogue_falls_back_to_english(monkeypatch):
    """Half a translated interface is worse than a consistent one."""
    import locale as _locale

    from app import i18n

    monkeypatch.setattr(_locale, "getlocale", lambda *a: ("is_IS", "UTF-8"))
    monkeypatch.setattr(_locale, "getdefaultlocale", lambda *a: ("is_IS", "UTF-8"))
    assert i18n.system_locale() == "en"


def test_a_stored_choice_beats_the_machine(tmp_path, monkeypatch):
    """Somebody who chose English on a French Mac meant it."""
    import locale as _locale

    from app.core import db
    from app.main import load_settings, save_locale

    monkeypatch.setattr(_locale, "getlocale", lambda *a: ("fr_FR", "UTF-8"))
    conn = db.connect(tmp_path / "t.sqlite3")
    db.migrate(conn)
    save_locale(conn, "en")
    settings = load_settings(conn)
    assert (settings.get("locale") or "system") == "en"
    conn.close()


# -- Restore must not claim a subscription nobody has -----------------------
#
# Observed on a real Mac, 2026-09-09: pressing "Restore purchase" BEFORE ever
# subscribing answered "Subscribed. The feed reads for you from tomorrow
# morning."
#
# These drove the old per-restore observer, whose success test was "a receipt
# file exists". That observer is gone; success now means the Worker confirmed
# the restored transaction with Apple. The same three cases are asserted here
# against the one observer's logic, and the rest in tests/test_storekit_hub.py.

def _restore_with(restored, answer):
    from types import SimpleNamespace

    from app.core.mac_storekit import DEFAULT_STATES, PRODUCT_ID, TransactionHub

    class Txn:
        def transactionState(self):
            return DEFAULT_STATES.restored

        def payment(self):
            return SimpleNamespace(productIdentifier=lambda: PRODUCT_ID)

        def transactionIdentifier(self):
            return "2000000111"

        def originalTransaction(self):
            return None

    queue = SimpleNamespace(finished=[], restoreCompletedTransactions=lambda: None)
    queue.finishTransaction_ = queue.finished.append
    seen = []
    hub = TransactionHub(queue=queue, exchange=lambda oid: answer,
                         run=lambda fn, done: done(fn()), emit=seen.append,
                         payment_for=lambda p: p)
    hub.restore()
    hub.transactions_updated(queue, [Txn() for _ in range(restored)])
    hub.restore_finished(queue)
    return seen[0] if seen else None, queue


def test_restore_with_nothing_to_restore_does_not_claim_a_subscription():
    from app.core.entitlement import AppleExchange
    from app.core.mac_storekit import Outcome

    result, _q = _restore_with(0, AppleExchange("licence", licence_key="K"))
    assert result is not None, "restore must always answer"
    assert result.outcome is not Outcome.PURCHASED, (
        "pressing Restore before subscribing said 'Subscribed.'")
    assert "othing to restore" in (result.detail or "")


def test_restore_with_a_real_transaction_does_report_it():
    """The positive control. A suite that only ever saw the empty case could
    not tell a working restore from one that always refuses."""
    from app.core.entitlement import AppleExchange
    from app.core.mac_storekit import Outcome

    result, queue = _restore_with(1, AppleExchange("licence", licence_key="K"))
    assert result.outcome is Outcome.PURCHASED
    assert len(queue.finished) == 1, (
        "an unfinished transaction is re-delivered on every launch for ever")


def test_a_restored_transaction_the_worker_could_not_confirm_is_not_success():
    from app.core.entitlement import AppleExchange
    from app.core.mac_storekit import Outcome

    result, queue = _restore_with(1, AppleExchange("unreachable"))
    assert result.outcome is not Outcome.PURCHASED
    assert queue.finished == [], "left for Apple to deliver again"


# -- the panel: no waiting on the UI thread, answers by signal ---------------

def test_subscribe_fetches_the_product_off_the_ui_thread_then_buys_on_it(
        qapp_and_settle):
    import threading

    _qapp, settle = qapp_and_settle
    from app.ui.settings import StoreKitEvents, SubscribePanel

    class NoProductYet(_NeverAnswers):
        def __init__(self):
            super().__init__()
            self.fetched_on = None
            self.bought_on = None

        def has_product(self):
            return False

        def load_product(self):
            self.fetched_on = threading.current_thread()
            return "product"

        def purchase(self):
            self.bought_on = threading.current_thread()
            return super().purchase()

    sk = NoProductYet()
    panel = SubscribePanel(storekit=sk, events=StoreKitEvents())
    settle(lambda: panel.buy.isEnabled(), what="the price")
    panel.buy.click()
    settle(lambda: sk.purchases == 1, what="the purchase")
    assert sk.fetched_on is not threading.main_thread(), "fetched on the UI thread"
    assert sk.bought_on is threading.main_thread(), "addPayment belongs on the main thread"
    panel.close()


def test_a_purchase_answer_reaches_the_panel_through_the_signal(qapp_and_settle):
    _qapp, settle = qapp_and_settle
    from app.core.mac_storekit import Outcome, Result
    from app.ui.settings import StoreKitEvents, SubscribePanel

    events = StoreKitEvents()
    panel = SubscribePanel(storekit=_NeverAnswers(), events=events)
    settle(lambda: panel.buy.isEnabled(), what="the price")

    events.finished.emit(Result(Outcome.DEFERRED, "waiting"))
    assert "approval" in panel.result.text()

    events.finished.emit(Result(Outcome.PURCHASED))
    assert "Subscribed" in panel.result.text()
    settle(lambda: True)
    panel.close()


def test_a_second_press_while_the_first_is_pending_says_so_without_an_error(
        qapp_and_settle):
    _qapp, settle = qapp_and_settle
    from app.core.mac_storekit import Outcome, Result
    from app.ui.settings import StoreKitEvents, SubscribePanel

    class Pending(_NeverAnswers):
        def purchase(self):
            super().purchase()
            return Result(Outcome.IN_PROGRESS) if self.purchases > 1 else None

    sk = Pending()
    panel = SubscribePanel(storekit=sk, events=StoreKitEvents())
    settle(lambda: panel.buy.isEnabled(), what="the price")
    panel._purchase()
    panel._purchase()
    said = panel.result.text().lower()
    assert "fail" not in said and "error" not in said
    panel.close()


# -- what guideline 3.1.2(c) requires the purchase screen to say -------------
#
# Version 1.1.0 (75) carried all of it in App Store Connect and none of it on
# the screen where the person actually buys, and was rejected for that. The
# metadata is not the purchase flow.

def test_the_purchase_screen_states_the_title_the_length_and_the_price(
        qapp_and_settle):
    _qapp, settle = qapp_and_settle
    from PySide6.QtWidgets import QLabel
    from app.ui.settings import SUBSCRIPTION_TITLE, SubscribePanel

    panel = SubscribePanel(storekit=_SlowStoreKit())
    settle(lambda: panel.price.text() == "$79.00", what="the price")

    shown = " ".join(label.text() for label in panel.findChildren(QLabel))
    assert SUBSCRIPTION_TITLE in shown, "the subscription is not named"
    assert "1 month" in shown, "the length of one term is not stated"
    assert "$79.00" in shown, "the price is not stated"
    panel.close()


def test_both_required_links_are_the_documents_they_claim_to_be(
        qapp_and_settle):
    """A test that asserts "a link exists" passes on a link to nowhere, so the
    URLs are asserted by value — they are what Apple was given and what the
    listing cites."""
    _qapp, settle = qapp_and_settle
    from app.ui.settings import APPLE_EULA_URL, PRIVACY_URL, SubscribePanel

    assert PRIVACY_URL == "https://dawnlist.spencerfields.com/privacy.html"
    assert APPLE_EULA_URL == (
        "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")

    panel = SubscribePanel(storekit=_SlowStoreKit())
    settle(lambda: panel.price.text() == "$79.00", what="the price")
    for link, url in ((panel.link_privacy, PRIVACY_URL),
                      (panel.link_eula, APPLE_EULA_URL)):
        assert f'href="{url}"' in link.text(), link.text()
        # A link that opens nothing is a link to nowhere with extra steps.
        assert link.openExternalLinks()
    panel.close()


def test_the_app_and_the_mac_listing_cite_the_same_eula():
    """A Mac subscriber is bound by the EULA the listing names and by nothing
    else. Two plausible links to two different agreements is worse than one."""
    import json

    from app.ui.settings import APPLE_EULA_URL

    listing = json.loads(
        (ROOT / "store" / "listing-mac" / "en.json").read_text(encoding="utf-8"))
    assert APPLE_EULA_URL in listing["description"]


def test_the_screen_names_the_subscription_apple_was_actually_given():
    """The title and the term shown before purchase come from the same place
    the storefront's do. A second name for one product is how somebody ends up
    unsure what they bought."""
    from app.i18n import tr
    from app.ui.settings import PRIVACY_URL, SUBSCRIPTION_TITLE

    asc = (ROOT / "tools" / "asc_subscription.py").read_text(encoding="utf-8")
    assert f'DISPLAY_NAME = "{SUBSCRIPTION_TITLE}"' in asc
    assert '"subscriptionPeriod": "ONE_MONTH"' in asc
    assert tr("settings.subscribe_length") == "1 month"

    listing = (ROOT / "tools" / "asc_listing.py").read_text(encoding="utf-8")
    assert f'PRIVACY_URL = "{PRIVACY_URL}"' in listing
