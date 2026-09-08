"""Which feed provider `build_provider` picks, and why the order matters.

This file exists because the managed provider was written, tested and then
reachable from nothing: `build_provider` constructed `TheirStackProvider`
unconditionally while its own docstring described routing a managed user
through the Worker. Tests passed, the module was never imported in production,
and PyInstaller did not even bundle it — the whole paid feed path was absent
from the shipped application.

So these tests assert on SELECTION, not on either provider's behaviour.
"""
from __future__ import annotations

import pytest

from app.main import NotConfigured, build_provider


@pytest.fixture
def no_keyring(monkeypatch):
    """Neither credential present unless a test puts one there."""
    store: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        @staticmethod
        def get_password(service, account):
            return store.get((service, account))

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyring)
    monkeypatch.setattr("app.core.entitlement.stored_licence", lambda: None)
    return store


def test_a_licence_routes_through_the_worker(no_keyring, monkeypatch):
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-AAAA-BBBB")
    prov = build_provider(conn=None)
    assert prov.name == "managed"


def test_the_licence_wins_when_both_credentials_exist(no_keyring, monkeypatch):
    """The case that costs money if it goes the other way.

    A subscriber with a leftover provider key must still go through the
    Worker: otherwise they bypass the cap they are paying for and spend
    credits that nothing is metering.
    """
    no_keyring[("dawnlist-feed", "api-key")] = "ts-developer-key"
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-AAAA-BBBB")
    prov = build_provider(conn=None)
    assert prov.name == "managed"


def test_a_raw_feed_key_is_the_developer_fallback(no_keyring):
    no_keyring[("dawnlist-feed", "api-key")] = "ts-developer-key"
    prov = build_provider(conn=None)
    assert prov.name != "managed"


def test_neither_credential_raises_and_names_the_licence_first(no_keyring, monkeypatch):
    """The message a real customer sees must lead with the licence.

    Telling a paying user to put a TheirStack key in their keyring is advice
    for a product they did not buy.

    The variant is PINNED. Without it this test read whichever build flag
    happened to be sitting in the source tree, so its result depended on what
    the developer last ran `set_build_variant.py` with — it passed for months
    only because that flag was `store`, and started failing the moment the
    store message stopped mentioning the keyring at all. A test whose subject
    is chosen by local state is not testing what it claims to.
    """
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "direct")
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    message = str(e.value)
    assert "licence" in message.lower()
    assert message.lower().index("licence") < message.lower().index("dawnlist-feed")


def test_the_managed_module_is_importable_from_a_frozen_build():
    """A guard against the module being dropped from the bundle again.

    PyInstaller follows static imports. `build_provider` imports
    `app.feed.managed` inside the function, which it does follow — but if that
    import is ever removed or made truly dynamic, the module silently stops
    being collected and the managed tier breaks only in the packaged app,
    where nobody runs the tests.
    """
    import app.feed.managed as managed
    assert hasattr(managed, "ManagedProvider")


# ---------------------------------------------------------------------------
# The store build has no feed route — the launch blocker
# ---------------------------------------------------------------------------

def test_a_store_build_without_a_licence_says_so_honestly(no_keyring, monkeypatch):
    """A paying store customer must not be told to do something impossible.

    `entitlement.require` treats a store build as entitled BY POSSESSION,
    which is sound for a one-time purchase and unsound here: the feed is
    metered per licence server-side, so possession gives the app nothing to
    meter against. The customer has paid and cannot run.

    Until a store purchase issues a licence, the least this can do is fail in
    words the person can act on. Telling them to "enter your licence key" is
    advice they cannot follow — no key was ever issued — and pointing them at
    a keyring entry is advice for a product they did not buy.
    """
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "store")
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    message = str(e.value)
    # Windows CAN accept a licence, so the message names the action the user
    # can take rather than reporting a fault they cannot do anything about.
    assert "licence" in message.lower()
    assert "settings" in message.lower()
    assert "keyring" not in message.lower()
    assert "dawnlist-feed" not in message


def test_the_mac_app_store_build_gets_the_same_treatment(no_keyring, monkeypatch):
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "keyring" not in str(e.value).lower()


def test_a_direct_build_is_still_told_about_the_purchase_email(no_keyring, monkeypatch):
    """The direct-download customer DOES have a key, and it came by email."""
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "direct")
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "purchase email" in str(e.value).lower()


def test_a_store_build_WITH_a_licence_uses_the_managed_feed(no_keyring, monkeypatch):
    """Once a store purchase does issue a licence, nothing else has to change."""
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "store")
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-AAAA-BBBB")
    assert build_provider(conn=None).name == "managed"


# ---------------------------------------------------------------------------
# Apple guideline 3.1.1 — licence keys are named as a prohibited mechanism
# ---------------------------------------------------------------------------

def test_a_mac_app_store_build_ignores_a_stored_licence(no_keyring, monkeypatch):
    """3.1.1: "Apps may not use their own mechanisms to unlock content or
    functionality, such as license keys..."

    Hiding the licence panel in the MAS build is not enough. The keyring is per
    USER, not per application: anyone who ran the direct-download build and
    later installed from the Mac App Store still has a licence in their
    credential store. Without this the MAS build would find it and quietly
    unlock a subscription bought outside Apple's commerce.

    That is not a hypothetical. It is what happens to the people most likely to
    buy from the store — the ones who tried the direct build first.
    """
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-BOUGHT-ELSEWHERE")
    monkeypatch.setattr("app.core.mac_receipt.read_receipt", lambda: None)
    # Refuses outright. It must NOT fall through to the stored key.
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "receipt" in str(e.value).lower()


def test_a_mac_build_uses_the_receipt_and_never_the_stored_key(monkeypatch, no_keyring):
    """Even with BOTH present, the Apple receipt is the only route taken."""
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-BOUGHT-ELSEWHERE")
    monkeypatch.setattr("app.core.mac_receipt.read_receipt", lambda: b"opaque")
    seen = {}

    def fake_exchange(receipt, **kw):
        seen["receipt"] = receipt
        return "DAWN-FROM-APPLE"

    monkeypatch.setattr("app.core.entitlement.exchange_mac_receipt", fake_exchange)
    prov = build_provider(conn=None)
    assert prov.name == "managed"
    assert seen["receipt"] == b"opaque"
    # The licence in play came from Apple, not from the credential store.
    assert prov._licence == "DAWN-FROM-APPLE"


def test_a_lapsed_mac_subscription_is_refused_not_guessed(monkeypatch, no_keyring):
    """Apple declining is a legitimate answer — lapsed, refunded, or a sandbox
    receipt against production — and must not fall back to anything."""
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-BOUGHT-ELSEWHERE")
    monkeypatch.setattr("app.core.mac_receipt.read_receipt", lambda: b"opaque")
    monkeypatch.setattr("app.core.entitlement.exchange_mac_receipt",
                        lambda r, **kw: None)
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "subscription" in str(e.value).lower()


def test_windows_store_is_deliberately_not_covered_by_that_rule(no_keyring, monkeypatch):
    """Microsoft permits third-party commerce, subject to declaring it in
    Partner Center. So a Windows Store build MAY honour a licence, and the
    restriction above is Apple's alone rather than a blanket rule."""
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "store")
    monkeypatch.setattr("app.core.entitlement.stored_licence",
                        lambda: "DAWN-AAAA-BBBB")
    assert build_provider(conn=None).name == "managed"
