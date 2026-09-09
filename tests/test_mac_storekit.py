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


def collect(fn, *args):
    """Run a callback-taking entry point and return the single Result."""
    got = []
    fn(got.append, *args)
    assert len(got) == 1, f"expected exactly one callback, got {len(got)}"
    return got[0]


# -- it is safe to import and call from anywhere -----------------------------

def test_importing_never_raises_off_macos():
    """The whole point of the degradation discipline. A module that can explode
    on import is one callers have to defend against, so they stop calling it."""
    assert mac_storekit.PRODUCT_ID == "com.spencerfields.dawnlist.monthly"


def test_available_is_false_off_macos():
    assert sys.platform != "darwin", "this test describes the non-Mac case"
    assert mac_storekit.available() is False


def test_can_make_payments_is_false_rather_than_raising():
    assert mac_storekit.can_make_payments() is False


def test_price_is_none_rather_than_a_guess():
    """None, never a hardcoded string. Apple sets the price per storefront
    across 175 territories, and showing a price that differs from what the
    App Store charges is a rejection."""
    assert mac_storekit.price() is None


# -- every purchase path refuses, and says so ---------------------------------

def test_purchase_refuses_instead_of_raising():
    result = collect(mac_storekit.purchase)
    assert result.outcome is Outcome.UNAVAILABLE
    assert result.detail, "a refusal with no reason is not actionable"


def test_restore_refuses_instead_of_raising():
    result = collect(mac_storekit.restore)
    assert result.outcome is Outcome.UNAVAILABLE
    assert result.detail


def test_refresh_receipt_reports_false_rather_than_raising():
    got = []
    mac_storekit.refresh_receipt(got.append)
    assert got == [False]


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
    AttributeError in front of a paying customer."""
    monkeypatch.setattr("app.core.mac_storekit.sys.platform", "darwin")
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    # StoreKit is genuinely absent here, so this exercises the real import
    # guard rather than a stub of it.
    assert mac_storekit.available() is False
