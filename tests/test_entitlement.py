"""Dawnlist is a PAID APP. These pin what that means."""
from datetime import datetime, timedelta, timezone

import pytest

import app.core.entitlement as ent
from app.core import db
from app.core.entitlement import Entitlement, NotEntitled, check, require

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    """Never read the real Credential Manager from a test."""
    monkeypatch.setattr(ent, "stored_licence", lambda: None)


def as_variant(monkeypatch, name):
    monkeypatch.setattr(ent, "variant", lambda: name)


# -- store builds -----------------------------------------------------------
def test_a_store_build_is_entitled_by_possession(conn, monkeypatch):
    """The Store does not hand the binary to someone who has not bought it.
    Re-asking adds a failure mode in exchange for nothing."""
    as_variant(monkeypatch, "store")
    e = check(conn, now=NOW)
    assert e.entitled and e.source == "store"
    assert "Microsoft Store" in e.reason


def test_a_mac_store_build_is_entitled_by_possession(conn, monkeypatch):
    as_variant(monkeypatch, "mas")
    assert check(conn, now=NOW).entitled


def test_a_store_build_never_calls_the_network(conn, monkeypatch):
    as_variant(monkeypatch, "store")

    def explode(_key):
        raise AssertionError("a store build must not verify anything remotely")

    assert check(conn, verifier=explode, now=NOW).entitled


def test_a_store_build_does_not_need_a_licence_key(conn, monkeypatch):
    as_variant(monkeypatch, "store")
    monkeypatch.setattr(ent, "stored_licence", lambda: None)
    assert check(conn, now=NOW).entitled


# -- direct download --------------------------------------------------------
def test_no_licence_means_no_run(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    e = check(conn, now=NOW)
    assert not e.entitled
    assert "No licence key found" in e.reason


def test_the_refusal_says_the_board_stays_open(conn, monkeypatch):
    """Locking someone out of their own tracker is a punishment, not a gate."""
    as_variant(monkeypatch, "direct")
    assert "stay open" in check(conn, now=NOW).reason


def test_a_valid_licence_runs(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-XXXX")
    e = check(conn, verifier=lambda k: True, now=NOW)
    assert e.entitled and e.source == "licence"


def test_a_refused_licence_does_not_run(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-BAD")
    e = check(conn, verifier=lambda k: False, now=NOW)
    assert not e.entitled and not e.unverifiable
    assert "not accepted" in e.reason


# -- the grace period, which is the point ----------------------------------
def test_an_outage_does_not_lock_out_someone_who_has_paid(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-XXXX")

    assert check(conn, verifier=lambda k: True, now=NOW).entitled   # verified
    later = NOW + timedelta(days=3)
    e = check(conn, verifier=lambda k: None, now=later)             # unreachable
    assert e.entitled and e.source == "grace"
    assert "cached licence" in e.reason


def test_the_grace_period_does_expire(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-XXXX")
    check(conn, verifier=lambda k: True, now=NOW)

    e = check(conn, verifier=lambda k: None, now=NOW + timedelta(days=20))
    assert not e.entitled and e.unverifiable


def test_a_REFUSAL_is_not_covered_by_grace(conn, monkeypatch):
    """Grace absorbs an outage, never a revoked licence — otherwise a refund
    keeps working for a fortnight."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-XXXX")
    check(conn, verifier=lambda k: True, now=NOW)

    e = check(conn, verifier=lambda k: False, now=NOW + timedelta(days=1))
    assert not e.entitled
    assert not e.unverifiable


def test_unreachable_and_refused_are_distinguishable(conn, monkeypatch):
    """Conflating them turns an outage into an accusation."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-XXXX")

    refused = check(conn, verifier=lambda k: False, now=NOW)
    unreachable = check(conn, verifier=lambda k: None, now=NOW)
    assert refused.unverifiable is False
    assert unreachable.unverifiable is True
    assert "nothing has been cancelled" in unreachable.reason.lower()


# -- the gate ---------------------------------------------------------------
def test_require_raises_when_not_entitled(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    with pytest.raises(NotEntitled, match="No licence key"):
        require(conn, now=NOW)


def test_require_passes_on_a_store_build(conn, monkeypatch):
    as_variant(monkeypatch, "store")
    assert require(conn, now=NOW).entitled


def test_the_run_is_gated_but_nothing_else_is(conn, monkeypatch):
    """Only morning_run calls require(). If this list grows, the gate has
    spread somewhere it was not meant to go."""
    import pathlib
    import re

    app_dir = pathlib.Path(ent.__file__).resolve().parents[1]
    callers = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"require_entitlement\(|entitlement\.require\(", text):
            callers.append(path.name)
    assert callers == ["main.py"], f"entitlement is enforced in {callers}"


# -- override codes ---------------------------------------------------------
def test_redeem_maps_server_errors_to_readable_reasons():
    from app.core.entitlement import _redeem_message
    assert "already been used" in _redeem_message("code_spent")
    assert "not recognised" in _redeem_message("invalid_code")
    assert "expired" in _redeem_message("expired_code")
    assert _redeem_message(None)          # never blank


def test_storing_a_licence_does_not_mark_it_verified(conn, monkeypatch):
    """A mistyped key must fail at the door, not be trusted because it was the
    most recent thing written."""
    saved = {}
    monkeypatch.setattr(ent, "store_licence",
                        lambda k: saved.update(key=k))
    ent.store_licence("DAWN-TYPO")
    assert saved["key"] == "DAWN-TYPO"
    assert ent._get(conn, ent.VERIFIED_AT) is None
