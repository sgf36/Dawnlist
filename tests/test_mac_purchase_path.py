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


def test_a_valid_receipt_becomes_a_licence():
    from app.core.entitlement import exchange_mac_receipt

    seen = []
    got = exchange_mac_receipt(b"receipt-bytes",
                               opener=_opener({"licence_key": "DAWN-MAC"},
                                              seen))
    assert got == "DAWN-MAC"
    sent = json.loads(seen[0].data.decode())
    assert base64.b64decode(sent["receipt"]) == b"receipt-bytes", (
        "the receipt goes as opaque bytes — the app forms no view of what is "
        "inside it, because a client-side check is one a user patches out")


def test_the_receipt_request_identifies_the_client():
    """The same Cloudflare 1010 that refused every other Worker call. This one
    is the Mac purchase path, so a 403 here means a paid subscription that
    reaches no feed."""
    from app.core.entitlement import exchange_mac_receipt
    from app.core.http import USER_AGENT

    seen = []
    exchange_mac_receipt(b"x", opener=_opener({"licence_key": "k"}, seen))
    assert seen[0].get_header("User-agent") == USER_AGENT


def test_no_active_subscription_is_an_answer_not_an_error():
    """Lapsed, refunded, or a sandbox receipt against production. All
    legitimate, none of them a failure to hide."""
    from app.core.entitlement import exchange_mac_receipt

    assert exchange_mac_receipt(b"x", opener=_opener({})) is None


def test_an_outage_is_not_a_refusal():
    from app.core.entitlement import exchange_mac_receipt

    def boom(_request, timeout=None):
        raise urllib.error.URLError("no network")

    assert exchange_mac_receipt(b"x", opener=boom) is None
