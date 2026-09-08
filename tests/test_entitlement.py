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


# -- the Microsoft Store build is NOT entitled by possession -----------------
#
# It was until 2026-09-08. The Store listing is FREE and Windows sells through
# Paddle on both channels, so possession would have handed every Store customer
# the whole subscription for nothing. These four tests exist to keep that hole
# shut; if one of them starts failing, read why before "fixing" it.

def test_a_microsoft_store_build_is_not_entitled_by_possession(conn, monkeypatch):
    """A free Store download with no key buys nothing."""
    as_variant(monkeypatch, "store")
    monkeypatch.setattr(ent, "stored_licence", lambda: None)
    e = check(conn, now=NOW)
    assert not e.entitled
    assert "licence key" in e.reason.lower()


def test_a_microsoft_store_build_is_entitled_by_a_paddle_licence(conn, monkeypatch):
    """The same key the direct-download build takes, on the same path."""
    as_variant(monkeypatch, "store")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-XXXX")
    e = check(conn, verifier=lambda _k: True, now=NOW)
    assert e.entitled and e.source == "licence"


def test_a_microsoft_store_build_does_verify_remotely(conn, monkeypatch):
    """The opposite of the old rule, and the point of the change: the Store
    build asks the Worker, because nothing else establishes that it was paid
    for."""
    as_variant(monkeypatch, "store")
    monkeypatch.setattr(ent, "stored_licence", lambda: "DAWN-XXXX")
    asked = []
    check(conn, verifier=lambda k: asked.append(k) or True, now=NOW)
    assert asked == ["DAWN-XXXX"]


# -- the Mac App Store build IS, because Apple forbids keys ------------------
def test_a_mac_store_build_is_entitled_without_a_key(conn, monkeypatch):
    """Guideline 3.1.1 leaves nothing for this gate to check. The real gate is
    `build_provider`, which trades the App Store receipt for a licence and
    refuses when Apple reports no active subscription."""
    as_variant(monkeypatch, "mas")
    monkeypatch.setattr(ent, "stored_licence", lambda: None)
    assert check(conn, now=NOW).entitled


def test_a_mac_store_build_never_calls_the_network(conn, monkeypatch):
    as_variant(monkeypatch, "mas")

    def explode(_key):
        raise AssertionError("a MAS build must not verify a key remotely")

    assert check(conn, verifier=explode, now=NOW).entitled


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


def test_require_refuses_a_store_build_with_no_licence(conn, monkeypatch):
    """The gate a free Store download actually meets."""
    as_variant(monkeypatch, "store")
    monkeypatch.setattr(ent, "stored_licence", lambda: None)
    with pytest.raises(NotEntitled, match="No licence key"):
        require(conn, now=NOW)


def test_require_passes_on_a_mac_store_build(conn, monkeypatch):
    as_variant(monkeypatch, "mas")
    assert require(conn, now=NOW).entitled


def test_the_gate_is_enforced_in_one_file_only(conn, monkeypatch):
    """Paid work is gated; onboarding, the board and every screen are not. If
    this list grows, the gate has spread somewhere it was not meant to go.

    Read as an AST, not grepped. The first version matched source text and a
    docstring that merely NAMED `entitlement.require()` failed it — a comment
    explaining a rule is not an application of it.
    """
    import ast
    import pathlib

    app_dir = pathlib.Path(ent.__file__).resolve().parents[1]
    callers = set()
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", ""))
            if name in {"require_entitlement", "require"} and _is_entitlement(func):
                callers.add(path.name)
    assert callers == {"main.py"}, f"entitlement is enforced in {sorted(callers)}"


def _is_entitlement(func) -> bool:
    """`require` is a common enough name to need disambiguating from a bare
    `require(...)` that has nothing to do with paying."""
    import ast
    if isinstance(func, ast.Attribute):
        return getattr(func.value, "id", "") == "entitlement"
    return getattr(func, "id", "") == "require_entitlement"


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
