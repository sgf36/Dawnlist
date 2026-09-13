"""The Store ID key is minted with the service ticket the Worker issues.

`GetCustomerCollectionsIdAsync` requires an Entra ID token with the audience
.../b2b/keys/create/collections, issued by the publisher's service
(learn.microsoft.com, "Manage product entitlements from a service", step 4).
The app passed an empty string and never asked the Worker for one, so no Store
subscriber could ever be confirmed. Found by audit, 2026-09-13.
"""
from __future__ import annotations

import io
import json
import urllib.error

from app.core import entitlement, msstore


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(routes, seen):
    def open_(request, timeout=None):
        url = request.full_url
        seen.append((request.get_method(), url))
        for suffix, answer in routes.items():
            if url.endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                return _Response(json.dumps(answer).encode())
        raise AssertionError(f"unexpected {url}")
    return open_


def test_the_ticket_is_fetched_and_passed_to_the_store(monkeypatch):
    given = []
    monkeypatch.setattr(msstore, "collections_key",
                        lambda **kw: given.append(kw) or "STORE-ID-KEY")
    monkeypatch.setattr(entitlement, "write_ms_cache", lambda cache: None)
    seen = []
    opener = _opener({"/v1/microsoft/ticket": {"ticket": "ENTRA-TICKET"},
                      "/v1/microsoft": {"licence_key": "DAWN-MS"}}, seen)

    result = entitlement.ms_exchange_and_cache(base="https://w.example", opener=opener)

    assert given == [{"service_ticket": "ENTRA-TICKET"}]
    assert seen[0] == ("GET", "https://w.example/v1/microsoft/ticket")
    assert seen[1] == ("POST", "https://w.example/v1/microsoft")
    assert result.outcome == "licence" and result.licence_key == "DAWN-MS"


def test_no_ticket_is_could_not_ask_and_the_store_is_not_troubled(monkeypatch):
    def explode(**kw):
        raise AssertionError("without a ticket there is no key to mint")

    monkeypatch.setattr(msstore, "collections_key", explode)
    opener = _opener({"/v1/microsoft/ticket": urllib.error.URLError("offline")}, [])
    result = entitlement.ms_exchange_and_cache(base="https://w.example", opener=opener)
    assert result.outcome == "unreachable"
