"""What can honestly be tested about StoreKit from a machine that is not a Mac.

BE CLEAR ABOUT WHAT THIS PROVES. It proves the module is SAFE to import and
call from Windows, from a direct-download build, and from a development run —
that every entry point degrades to a refusal instead of raising, and that no
purchase path can be reached from a build that must not sell through Apple.

IT PROVES NOTHING ABOUT WHETHER A PURCHASE WORKS. Not one StoreKit call is
exercised here, because there is no StoreKit on this machine. A green run of
this file is evidence about the guards, not about the feature. Anyone reading
these passes as "the Mac App Store build is ready" has made exactly the mistake
this repository keeps recording.
"""
import sys

import pytest

from app.core import mac_storekit
from app.core.mac_storekit import Outcome



# -- it is safe to import and call from anywhere -----------------------------

def test_importing_never_raises_off_macos():
    """The whole point of the degradation discipline. A module that can explode
    on import is one callers have to defend against, so they stop calling it."""
    assert mac_storekit.PRODUCT_ID == "com.spencerfields.dawnlist.monthly"


@pytest.mark.skipif(sys.platform == "darwin",
                    reason="describes the NON-Mac case; on a Mac, availability "
                           "depends on PyObjC and the build variant, which the "
                           "variant tests below cover properly")
def test_available_is_false_off_macos():
    # This asserted `sys.platform != "darwin"` as a way of documenting its own
    # scope, which made it FAIL on the macOS CI runner rather than skip — the
    # first thing the new Mac leg did was go red on a test that was never
    # meant to run there. A precondition is a skip, not an assertion.
    assert mac_storekit.available() is False


def test_can_make_payments_is_false_rather_than_raising():
    assert mac_storekit.can_make_payments() is False


def test_price_is_none_rather_than_a_guess():
    """None, never a hardcoded string. Apple sets the price per storefront
    across 175 territories, and showing a price that differs from what the
    App Store charges is a rejection."""
    assert mac_storekit.price() is None


# -- every purchase path refuses, and says so ---------------------------------

# A refusal is RETURNED now rather than passed to a callback: the answer to a
# purchase that did start arrives through the one observer's signal, so only
# "could not start" is the caller's to hear directly. `refresh_receipt` is
# gone, with its test — nothing waits for a receipt file once the Worker
# confirms the transaction id with Apple.

def test_purchase_refuses_instead_of_raising():
    result = mac_storekit.purchase()
    assert result.outcome is Outcome.UNAVAILABLE
    assert result.detail, "a refusal with no reason is not actionable"


def test_restore_refuses_instead_of_raising():
    result = mac_storekit.restore()
    assert result.outcome is Outcome.UNAVAILABLE
    assert result.detail


# -- the variant gate, which is the part that must never regress --------------

@pytest.mark.parametrize("build", ["store", "direct", "none", "ambiguous"])
def test_only_a_mas_build_could_ever_purchase(monkeypatch, build):
    """A DIRECT-DOWNLOAD BUILD ON A MAC MUST NOT SELL THROUGH APPLE.

    It already sells through Paddle. If both routes were live on one build a
    customer could pay twice, and Apple would be taking 15% of a sale it did
    not make. `available()` gates on the variant for that reason and not merely
    for tidiness.
    """
    monkeypatch.setattr("app.core.mac_storekit.sys.platform", "darwin")
    monkeypatch.setattr("app.core.build_variant.variant", lambda: build)
    assert mac_storekit.available() is False


def test_a_mas_build_still_needs_storekit_present(monkeypatch):
    """Claiming to be `mas` is not enough. Without the framework there is
    nothing to call, and reporting True would turn a clean refusal into an
    AttributeError in front of a paying customer.

    ABSENCE IS NOW SIMULATED, AND THAT CHANGE IS THE POINT. This test used to
    rely on StoreKit being genuinely missing from the machine — which it was,
    everywhere, because PyObjC was never a declared dependency. So the test
    passed by encoding the bug as the expected state, and went green on the
    macOS runner for the same reason the shipped package could not take a
    payment.

    Declaring PyObjC on 2026-09-09 made it FAIL, on a real Mac, with
    `assert True is False` — the first evidence from CI that the purchase path
    had become possible at all.
    """
    import builtins

    monkeypatch.setattr("app.core.mac_storekit.sys.platform", "darwin")
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")

    real_import = builtins.__import__

    def refuse_storekit(name, *args, **kwargs):
        if name == "StoreKit":
            raise ImportError("simulated: PyObjC not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_storekit)
    assert mac_storekit.available() is False


def test_a_mas_build_with_storekit_present_is_available(monkeypatch):
    """The other half, which nothing asserted while it could never be true.

    A test suite that only ever saw the unavailable branch could not tell a
    working purchase path from a missing library, which is exactly how a
    package that cannot charge anybody shipped, signed and reached TestFlight.
    """
    import sys as _sys
    import types

    monkeypatch.setattr("app.core.mac_storekit.sys.platform", "darwin")
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    monkeypatch.setitem(_sys.modules, "StoreKit", types.ModuleType("StoreKit"))
    assert mac_storekit.available() is True
