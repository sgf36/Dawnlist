"""The Mac App Store subscription, from the client's side of `/v1/apple`.

The client posted a receipt to a route that did not exist, and its
`except Exception: return None` turned the 404 into "the App Store could not
confirm an active subscription". These pin the contract that replaced it:

  * the StoreKit ORIGINAL TRANSACTION ID is what is sent, and remembered
    before asking;
  * three outcomes, never two — Apple refusing, a licence, or "could not ask";
  * a receipt only when no id was ever kept, so a customer who subscribed
    through the build in review is not told they never did.
"""
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

import app.core.entitlement as ent
from app.core.credentials import KeyringUnavailable
from app.core.entitlement import AppleExchange

NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _answer(status, payload, seen):
    def opener(request, timeout=None):
        seen.append(request)
        raw = json.dumps(payload).encode() if payload is not None else None
        if status == 200:
            return _Response(raw)
        raise urllib.error.HTTPError(request.full_url, status, "no", {},
                                     io.BytesIO(raw) if raw is not None else None)
    return opener


# -- the request and its three outcomes --------------------------------------

def test_the_original_transaction_id_is_what_is_sent():
    from app.core.http import USER_AGENT

    seen = []
    result = ent.exchange_apple(
        original_transaction_id="2000000111",
        opener=_answer(200, {"licence_key": "DAWN-MAC", "status": "active",
                             "expires_at": "2026-10-12T08:00:00.000Z",
                             "original_transaction_id": "2000000111"}, seen))
    assert result.outcome == "licence" and result.licence_key == "DAWN-MAC"
    assert result.expires_at == "2026-10-12T08:00:00.000Z"
    assert seen[0].full_url.endswith("/v1/apple")
    assert json.loads(seen[0].data.decode()) == {"originalTransactionId": "2000000111"}
    assert seen[0].get_header("User-agent") == USER_AGENT


@pytest.mark.parametrize("error", sorted(ent.APPLE_REFUSALS))
def test_apples_own_refusals_are_refusals(error):
    result = ent.exchange_apple(
        original_transaction_id="1",
        opener=_answer(403, {"error": error, "status": "expired"}, []))
    assert result.outcome == "refused"
    assert result.licence_key is None


@pytest.mark.parametrize("status,payload", [
    (503, {"error": "apple_not_configured"}),   # the Worker is not set up yet
    (502, {"error": "apple_auth_failed"}),      # our key, not the customer
    (403, None),                                # Cloudflare's HTML 403
    (404, {"error": "not_found"}),              # a Worker without the route
])
def test_anything_that_is_not_apple_refusing_is_unreachable(status, payload):
    result = ent.exchange_apple(original_transaction_id="1",
                                opener=_answer(status, payload, []))
    assert result.outcome == "unreachable"


def test_a_network_failure_is_unreachable():
    def down(request, timeout=None):
        raise urllib.error.URLError("no network")

    assert ent.exchange_apple(original_transaction_id="1",
                              opener=down).outcome == "unreachable"


def test_a_reply_without_a_licence_is_not_a_licence():
    assert ent.exchange_apple(original_transaction_id="1",
                              opener=_answer(200, {}, [])).outcome == "unreachable"


# -- remembering what Apple said ---------------------------------------------

@pytest.fixture
def cache(monkeypatch):
    """The credential-store entry, in memory."""
    box = {"c": {}}
    monkeypatch.setattr(ent, "apple_cache", lambda: dict(box["c"]))
    monkeypatch.setattr(ent, "write_apple_cache",
                        lambda c: box.update(c=dict(c)))
    return box


def test_the_id_is_remembered_before_asking(cache, monkeypatch):
    """StoreKit hands the id over only while delivering the transaction, so a
    purchase made during an outage must still be askable about tomorrow."""
    monkeypatch.setattr(ent, "exchange_apple",
                        lambda **kw: AppleExchange("unreachable"))
    assert ent.exchange_and_cache("2000", now=NOW).outcome == "unreachable"
    assert cache["c"]["original_transaction_id"] == "2000"


def test_a_licence_is_kept_with_its_expiry(cache, monkeypatch):
    monkeypatch.setattr(ent, "exchange_apple", lambda **kw: AppleExchange(
        "licence", licence_key="DAWN-MAC", expires_at="2026-10-12T08:00:00Z"))
    ent.exchange_and_cache("2000", now=NOW)
    assert cache["c"]["licence_key"] == "DAWN-MAC"
    assert cache["c"]["expires_at"] == "2026-10-12T08:00:00Z"
    assert cache["c"]["checked_at"] == NOW.isoformat(timespec="seconds")


def test_a_refusal_drops_the_licence_but_keeps_the_id(cache, monkeypatch):
    cache["c"] = {"original_transaction_id": "2000", "licence_key": "DAWN-OLD",
                  "checked_at": NOW.isoformat()}
    monkeypatch.setattr(ent, "exchange_apple",
                        lambda **kw: AppleExchange("refused", error="not_subscribed"))
    ent.exchange_and_cache(now=NOW)
    assert "licence_key" not in cache["c"]
    assert cache["c"]["original_transaction_id"] == "2000"


def test_an_unreachable_answer_keeps_the_last_licence(cache, monkeypatch):
    """The positive control for the refusal above."""
    cache["c"] = {"original_transaction_id": "2000", "licence_key": "DAWN-OLD"}
    monkeypatch.setattr(ent, "exchange_apple",
                        lambda **kw: AppleExchange("unreachable"))
    ent.exchange_and_cache(now=NOW)
    assert cache["c"]["licence_key"] == "DAWN-OLD"


def test_with_no_id_and_no_receipt_nothing_is_asked(cache, monkeypatch):
    def explode(**kw):
        raise AssertionError("there was nothing to ask with")

    monkeypatch.setattr(ent, "exchange_apple", explode)
    monkeypatch.setattr("app.core.mac_receipt.read_receipt", lambda: None)
    assert ent.exchange_and_cache(now=NOW).outcome == "none"


def test_a_customer_from_the_receipt_build_is_asked_by_receipt_once(cache, monkeypatch):
    """Subscribed through the build in review, which kept no id. The receipt
    is sent once, and the id the Worker returns is what is asked with next."""
    asked = []

    def exchange(**kw):
        asked.append(kw)
        return AppleExchange("licence", licence_key="DAWN-MAC",
                             original_transaction_id="2000")

    monkeypatch.setattr(ent, "exchange_apple", exchange)
    monkeypatch.setattr("app.core.mac_receipt.read_receipt", lambda: b"receipt")
    ent.exchange_and_cache(now=NOW)
    ent.exchange_and_cache(now=NOW)
    assert asked[0].get("receipt") == b"receipt"
    assert asked[1].get("original_transaction_id") == "2000"
    assert "receipt" not in asked[1] or asked[1]["receipt"] is None


def test_a_store_that_will_not_save_is_reported_not_hidden(cache, monkeypatch):
    def refuse(_c):
        raise KeyringUnavailable("locked")

    monkeypatch.setattr(ent, "write_apple_cache", refuse)
    monkeypatch.setattr(ent, "exchange_apple", lambda **kw: AppleExchange(
        "licence", licence_key="DAWN-MAC"))
    result = ent.exchange_and_cache("2000", now=NOW)
    assert result.outcome == "licence"
    assert result.saved is False


def test_a_licence_confirmed_minutes_ago_is_fresh():
    recent = {"licence_key": "K",
              "checked_at": (NOW - timedelta(minutes=5)).isoformat()}
    stale = {"licence_key": "K",
             "checked_at": (NOW - timedelta(minutes=20)).isoformat()}
    ahead = {"licence_key": "K",
             "checked_at": (NOW + timedelta(hours=2)).isoformat()}
    assert ent.fresh_apple_licence(recent, now=NOW) == "K"
    assert ent.fresh_apple_licence(stale, now=NOW) is None
    assert ent.fresh_apple_licence(ahead, now=NOW) is None


# -- the feed, on a Mac build ------------------------------------------------

@pytest.fixture
def mas(monkeypatch):
    monkeypatch.setattr("app.core.build_variant.variant", lambda: "mas")
    # No comp-code licence in play: these are about the Apple route.
    monkeypatch.setattr("app.core.entitlement.stored_licence", lambda: None)


def _cached(monkeypatch, **entry):
    monkeypatch.setattr("app.core.entitlement.apple_cache", lambda: dict(entry))


def _exchange(monkeypatch, result):
    monkeypatch.setattr("app.core.entitlement.exchange_and_cache",
                        lambda *a, **k: result)


def test_a_licence_confirmed_at_the_door_is_not_asked_for_twice(mas, monkeypatch):
    from app.main import build_provider

    _cached(monkeypatch, licence_key="DAWN-FRESH",
            checked_at=datetime.now(timezone.utc).isoformat())

    def explode(*a, **k):
        raise AssertionError("confirmed minutes ago; asking again learns nothing")

    monkeypatch.setattr("app.core.entitlement.exchange_and_cache", explode)
    assert build_provider(conn=None)._licence == "DAWN-FRESH"


def test_an_unreachable_worker_runs_on_the_last_licence(mas, monkeypatch):
    from app.main import build_provider

    _cached(monkeypatch, original_transaction_id="2000", licence_key="DAWN-LAST")
    _exchange(monkeypatch, AppleExchange("unreachable"))
    assert build_provider(conn=None)._licence == "DAWN-LAST"


def test_unreachable_with_nothing_kept_says_unreachable_not_lapsed(mas, monkeypatch):
    from app.main import NotConfigured, build_provider

    _cached(monkeypatch, original_transaction_id="2000")
    _exchange(monkeypatch, AppleExchange("unreachable"))
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "could not reach" in str(e.value).lower()
    assert "not active" not in str(e.value)


def test_a_refusal_is_never_softened_by_a_kept_licence(mas, monkeypatch):
    from app.main import NotConfigured, build_provider

    _cached(monkeypatch, original_transaction_id="2000", licence_key="DAWN-OLD")
    _exchange(monkeypatch, AppleExchange("refused", error="not_subscribed"))
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "not active" in str(e.value)


def test_nothing_to_ask_with_points_at_subscribe_and_restore(mas, monkeypatch):
    from app.main import NotConfigured, build_provider

    _exchange(monkeypatch, AppleExchange("none"))
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "Restore purchase" in str(e.value)


def test_an_unreadable_credential_store_says_so(mas, monkeypatch):
    from app.main import NotConfigured, build_provider

    def locked():
        raise KeyringUnavailable("locked")

    monkeypatch.setattr("app.core.entitlement.apple_cache", locked)
    with pytest.raises(NotConfigured) as e:
        build_provider(conn=None)
    assert "credential store" in str(e.value)
