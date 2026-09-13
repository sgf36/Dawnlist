"""HTTPS on the Mac verifies through the system trust store.

Build 173 on the cloud Mac, 2026-09-13: the /v1/apple exchange failed in under
a second with no error text, and the app bundle held no certificate file. See
`app.core.http.use_system_trust`.
"""
from __future__ import annotations

import sys
import types
import urllib.error

from app.core import http


def test_truststore_is_injected_on_macos(monkeypatch):
    calls = []
    fake = types.ModuleType("truststore")
    fake.inject_into_ssl = lambda: calls.append("injected")
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert http.use_system_trust("darwin") is True
    assert calls == ["injected"]


def test_windows_keeps_its_own_certificate_store(monkeypatch):
    """The positive control: the working Windows trust path is not touched."""
    calls = []
    fake = types.ModuleType("truststore")
    fake.inject_into_ssl = lambda: calls.append("injected")
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert http.use_system_trust("win32") is False
    assert calls == []


def test_a_broken_truststore_never_stops_the_app(monkeypatch):
    fake = types.ModuleType("truststore")

    def boom():
        raise RuntimeError("no Security framework")

    fake.inject_into_ssl = boom
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert http.use_system_trust("darwin") is False


def test_truststore_is_actually_installed():
    """It is in the lock only as someone else's dependency. The injection is
    guarded, so if it ever dropped out the Mac would silently lose HTTPS
    again. This makes that loud."""
    import truststore  # noqa: F401


def test_an_exchange_that_cannot_connect_names_the_reason():
    from app.core.entitlement import exchange_apple

    def opener(_request, timeout=None):
        raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    result = exchange_apple(original_transaction_id="2000000999", opener=opener)
    assert result.outcome == "unreachable"
    assert "CERTIFICATE_VERIFY_FAILED" in result.error
