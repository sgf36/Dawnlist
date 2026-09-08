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


def test_neither_credential_raises_and_names_the_licence_first(no_keyring):
    """The message a real customer sees must lead with the licence.

    Telling a paying user to put a TheirStack key in their keyring is advice
    for a product they did not buy.
    """
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
