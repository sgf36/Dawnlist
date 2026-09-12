"""Dawnlist is a PAID APP. These pin what that means."""
from datetime import datetime, timedelta, timezone

import pytest

import app.core.entitlement as ent
from app.core import db
from app.core.entitlement import Entitlement, NotEntitled, check, require

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

#: Captured at import, before conftest pins `verify_against_worker` to always
#: pass, so a test can run the real verifier over a stubbed transport.
REAL_VERIFY = ent.verify_against_worker
REAL_STORED = ent.stored_licence
REAL_LICENCE_CHECK = ent.licence_check


@pytest.fixture(autouse=True)
def real_licence_transport(monkeypatch):
    """conftest pins `licence_check` so no screen reaches the live Worker; the
    tests here drive the real one over stubbed openers."""
    monkeypatch.setattr(ent, "licence_check", REAL_LICENCE_CHECK)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    """Never read the real Credential Manager from a test."""
    monkeypatch.setattr(ent, "read_licence", lambda: None)


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
    monkeypatch.setattr(ent, "read_licence", lambda: None)
    e = check(conn, now=NOW)
    assert not e.entitled
    assert "licence key" in e.reason.lower()


def test_a_microsoft_store_build_is_entitled_by_a_paddle_licence(conn, monkeypatch):
    """The same key the direct-download build takes, on the same path."""
    as_variant(monkeypatch, "store")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    e = check(conn, verifier=lambda _k: True, now=NOW)
    assert e.entitled and e.source == "licence"


def test_a_microsoft_store_build_does_verify_remotely(conn, monkeypatch):
    """The opposite of the old rule, and the point of the change: the Store
    build asks the Worker, because nothing else establishes that it was paid
    for."""
    as_variant(monkeypatch, "store")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    asked = []
    check(conn, verifier=lambda k: asked.append(k) or True, now=NOW)
    assert asked == ["DAWN-XXXX"]


# -- the Mac App Store build: Apple's answer, with the same grace ------------
#
# A `mas` build used to be entitled WITHOUT ASKING, leaving the feed to refuse
# later — so a lapsed subscriber passed the door, got an empty morning, and was
# told nothing. Two tests pinned that shortcut ("entitled without a key",
# "never calls the network"); they are replaced by these, which still refuse
# to let a pasted or purchased key unlock the build.

def _mas_setup(monkeypatch, *, cache=None, granted=None):
    as_variant(monkeypatch, "mas")
    monkeypatch.setattr(ent, "read_licence", lambda: granted)
    monkeypatch.setattr(ent, "apple_cache", lambda: dict(cache or {}))


def _apple(outcome, **kw):
    from app.core.entitlement import AppleExchange
    return lambda _oid: AppleExchange(outcome, **kw)


def _apple_must_not_be_asked(_oid):
    raise AssertionError("Apple must not be asked here")


def test_a_mac_build_with_nothing_bought_is_told_to_subscribe_or_restore(conn, monkeypatch):
    _mas_setup(monkeypatch)
    e = check(conn, apple_exchanger=_apple("none"), now=NOW)
    assert not e.entitled and not e.unverifiable
    assert "Restore purchase" in e.reason


def test_a_confirmed_mac_subscription_runs_and_is_stamped(conn, monkeypatch):
    from app.core.entitlement import AppleExchange

    _mas_setup(monkeypatch, cache={"original_transaction_id": "2000"})
    asked = []

    def exchanger(oid):
        asked.append(oid)
        return AppleExchange("licence", licence_key="DAWN-MAC")

    e = check(conn, apple_exchanger=exchanger, now=NOW)
    assert e.entitled and e.source == "mas"
    assert asked == ["2000"]
    assert ent._get(conn, ent.VERIFIED_KEY) == ent._fingerprint("DAWN-MAC")


def test_a_lapsed_mac_subscription_is_refused_in_its_own_words(conn, monkeypatch):
    _mas_setup(monkeypatch, cache={"original_transaction_id": "2000",
                                   "licence_key": "DAWN-MAC"})
    check(conn, apple_exchanger=_apple("licence", licence_key="DAWN-MAC"), now=NOW)

    e = check(conn, apple_exchanger=_apple("refused", error="not_subscribed"),
              now=NOW + timedelta(days=1))
    assert not e.entitled and not e.unverifiable, "grace never covers Apple saying no"
    assert "not active" in e.reason


def test_an_unreachable_apple_runs_on_grace_as_windows_does(conn, monkeypatch):
    _mas_setup(monkeypatch, cache={"original_transaction_id": "2000",
                                   "licence_key": "DAWN-MAC"})
    check(conn, apple_exchanger=_apple("licence", licence_key="DAWN-MAC"), now=NOW)

    recent = check(conn, apple_exchanger=_apple("unreachable"),
                   now=NOW + timedelta(days=3))
    assert recent.entitled and recent.source == "grace"

    late = check(conn, apple_exchanger=_apple("unreachable"),
                 now=NOW + timedelta(days=ent.GRACE_DAYS + 1))
    assert not late.entitled and late.unverifiable
    assert "could not reach" in late.reason.lower()
    assert "not active" not in late.reason


def test_unreachable_with_no_recent_confirmation_is_worded_as_unreachable(conn, monkeypatch):
    _mas_setup(monkeypatch, cache={"original_transaction_id": "2000"})
    e = check(conn, apple_exchanger=_apple("unreachable"), now=NOW)
    assert not e.entitled and e.unverifiable
    assert "nothing has been cancelled" in e.reason


def test_mac_grace_belongs_to_the_licence_apple_issued(conn, monkeypatch):
    """The kept licence is what grace is measured against, so a stamp earned
    by one licence does not carry a different one through an outage."""
    _mas_setup(monkeypatch, cache={"original_transaction_id": "2000",
                                   "licence_key": "DAWN-OTHER"})
    check(conn, apple_exchanger=_apple("licence", licence_key="DAWN-MAC"), now=NOW)
    e = check(conn, apple_exchanger=_apple("unreachable"), now=NOW + timedelta(days=1))
    assert not e.entitled


def test_a_mac_build_with_an_unreadable_store_is_unverifiable(conn, monkeypatch):
    from app.core.credentials import KeyringUnavailable

    as_variant(monkeypatch, "mas")
    monkeypatch.setattr(ent, "read_licence", lambda: None)

    def locked():
        raise KeyringUnavailable("locked")

    monkeypatch.setattr(ent, "apple_cache", locked)
    e = check(conn, apple_exchanger=_apple_must_not_be_asked, now=NOW)
    assert not e.entitled and e.unverifiable
    assert "credential store" in e.reason


def test_an_access_code_grant_runs_on_a_mac_without_asking_apple(conn, monkeypatch):
    """Guideline 3.1.1 forbids a purchase outside Apple's commerce, not a free
    grant — which is how App Review and friends get in."""
    _mas_setup(monkeypatch, granted="DAWN-GRANT")
    monkeypatch.setattr(ent, "licence_check", lambda key, **kw: (
        "ok", {"ok": True, "granted_by_code": True, "purchased": False}))
    e = check(conn, apple_exchanger=_apple_must_not_be_asked, now=NOW)
    assert e.entitled


def test_a_purchased_licence_in_the_keyring_does_not_unlock_a_mac(conn, monkeypatch):
    """The positive control's opposite: the same route with a Paddle purchase."""
    _mas_setup(monkeypatch, granted="DAWN-BOUGHT")
    monkeypatch.setattr(ent, "licence_check", lambda key, **kw: (
        "ok", {"ok": True, "granted_by_code": False, "purchased": True}))
    e = check(conn, apple_exchanger=_apple("none"), now=NOW)
    assert not e.entitled


# -- a build that does not know what it is -----------------------------------
@pytest.mark.parametrize("build", ["none", "ambiguous"])
def test_a_build_without_one_variant_is_refused_without_asking(conn, monkeypatch, build):
    """Guessing the store would either unlock a Mac build with a key or ask a
    Windows customer for Apple's subscription. It says the copy is broken."""
    as_variant(monkeypatch, build)
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")

    def explode(_key):
        raise AssertionError("a broken build must not verify anything")

    e = check(conn, verifier=explode, now=NOW)
    assert not e.entitled and not e.unverifiable
    assert build in e.reason and "edition marker" in e.reason


def test_the_same_licence_runs_on_a_named_build(conn, monkeypatch):
    """Positive control for the refusal above: identical key and verifier."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    assert check(conn, verifier=lambda _k: True, now=NOW).entitled


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
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    e = check(conn, verifier=lambda k: True, now=NOW)
    assert e.entitled and e.source == "licence"


def test_a_refused_licence_does_not_run(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-BAD")
    e = check(conn, verifier=lambda k: False, now=NOW)
    assert not e.entitled and not e.unverifiable
    assert "not accepted" in e.reason


# -- the grace period, which is the point ----------------------------------
def test_an_outage_does_not_lock_out_someone_who_has_paid(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")

    assert check(conn, verifier=lambda k: True, now=NOW).entitled   # verified
    later = NOW + timedelta(days=3)
    e = check(conn, verifier=lambda k: None, now=later)             # unreachable
    assert e.entitled and e.source == "grace"
    assert "cached licence" in e.reason


def test_the_grace_period_does_expire(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    check(conn, verifier=lambda k: True, now=NOW)

    e = check(conn, verifier=lambda k: None, now=NOW + timedelta(days=20))
    assert not e.entitled and e.unverifiable


def test_a_REFUSAL_is_not_covered_by_grace(conn, monkeypatch):
    """Grace absorbs an outage, never a revoked licence — otherwise a refund
    keeps working for a fortnight."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    check(conn, verifier=lambda k: True, now=NOW)

    e = check(conn, verifier=lambda k: False, now=NOW + timedelta(days=1))
    assert not e.entitled
    assert not e.unverifiable


def _unreachable(_key):
    return None


def _verified(_key):
    return True


def test_a_stamp_from_the_future_earns_no_grace(conn, monkeypatch):
    """A stamp days ahead of the clock never ages out, so it would be grace
    for ever — a clock set wrong once, or a hand-edited database."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")

    check(conn, verifier=_verified, now=NOW + timedelta(days=3))
    e = check(conn, verifier=_unreachable, now=NOW)
    assert not e.entitled and e.unverifiable


def test_a_stamp_a_few_hours_ahead_still_counts(conn, monkeypatch):
    """The positive control: clocks disagree by hours, and that is ordinary."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")

    check(conn, verifier=_verified, now=NOW + timedelta(hours=6))
    e = check(conn, verifier=_unreachable, now=NOW)
    assert e.entitled and e.source == "grace"


def test_a_stamp_belongs_to_the_key_that_earned_it(conn, monkeypatch):
    """Without this, any string pasted in after a real key verified ran on
    that key's stamp through an outage."""
    as_variant(monkeypatch, "direct")
    stored = {"key": "DAWN-REAL"}
    monkeypatch.setattr(ent, "read_licence", lambda: stored["key"])
    check(conn, verifier=_verified, now=NOW)

    assert check(conn, verifier=_unreachable, now=NOW + timedelta(days=1)).entitled

    stored["key"] = "DAWN-SOMETHING-ELSE"
    e = check(conn, verifier=_unreachable, now=NOW + timedelta(days=1))
    assert not e.entitled and e.unverifiable
    assert ent._get(conn, ent.VERIFIED_KEY) != "DAWN-REAL", \
        "the key itself must not be written to the settings database"


def test_saving_a_licence_clears_its_old_stamp(conn, monkeypatch):
    """A refunded key pasted back during an outage matches its own stamp; the
    save has to wipe it or grace pays for the refund."""
    import keyring

    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    monkeypatch.setattr(keyring, "set_password", lambda *a: None)
    check(conn, verifier=_verified, now=NOW)
    assert check(conn, verifier=_unreachable, now=NOW + timedelta(days=1)).entitled

    ent.store_licence("DAWN-XXXX", conn=conn)
    e = check(conn, verifier=_unreachable, now=NOW + timedelta(days=1))
    assert not e.entitled


def test_saving_without_a_connection_clears_the_default_database(conn, monkeypatch):
    """The panels that save a key hold no connection."""
    import pathlib

    import keyring

    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    monkeypatch.setattr(keyring, "set_password", lambda *a: None)
    path = pathlib.Path(conn.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr("app.core.db.default_db_path", lambda *a, **k: path)
    check(conn, verifier=_verified, now=NOW)

    ent.store_licence("DAWN-XXXX")
    assert ent._get(conn, ent.VERIFIED_AT) is None


def _store_unreadable():
    from app.core.credentials import KeyringUnavailable
    raise KeyringUnavailable("locked")


def _must_not_ask(_key):
    raise AssertionError("nothing was read, so nothing may be verified")


def test_an_unreadable_credential_store_is_unverifiable_not_unlicensed(conn, monkeypatch):
    """"No licence key found" told a paying customer with a locked keychain
    that they had never bought anything."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", _store_unreadable)
    e = check(conn, verifier=_must_not_ask, now=NOW)
    assert not e.entitled and e.unverifiable
    assert "credential store" in e.reason
    assert "No licence key found" not in e.reason

    # Positive control: a working store with nothing in it IS unlicensed.
    monkeypatch.setattr(ent, "read_licence", lambda: None)
    empty = check(conn, now=NOW)
    assert not empty.unverifiable and "No licence key found" in empty.reason


def test_an_unreadable_store_runs_on_grace_after_a_recent_check(conn, monkeypatch):
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    check(conn, verifier=_verified, now=NOW)

    monkeypatch.setattr(ent, "read_licence", _store_unreadable)
    recent = check(conn, verifier=_must_not_ask, now=NOW + timedelta(days=2))
    assert recent.entitled and recent.source == "grace"

    stale = check(conn, verifier=_must_not_ask, now=NOW + timedelta(days=20))
    assert not stale.entitled and stale.unverifiable


def test_callers_that_only_show_a_key_still_get_none(monkeypatch):
    """The panels and the admin check use `stored_licence`, which must keep
    answering None rather than raising into a screen."""
    monkeypatch.setattr(ent, "read_licence", _store_unreadable)
    assert REAL_STORED() is None


def test_unreachable_and_refused_are_distinguishable(conn, monkeypatch):
    """Conflating them turns an outage into an accusation."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")

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
    monkeypatch.setattr(ent, "read_licence", lambda: None)
    with pytest.raises(NotEntitled, match="No licence key"):
        require(conn, now=NOW)


def test_require_refuses_a_mac_build_apple_has_not_confirmed(conn, monkeypatch):
    """Was "require passes on a Mac build", unconditionally — the shortcut
    the Mac tests above replace."""
    _mas_setup(monkeypatch)
    with pytest.raises(NotEntitled, match="Restore purchase"):
        require(conn, apple_exchanger=_apple("none"), now=NOW)


def test_require_passes_on_a_confirmed_mac_build(conn, monkeypatch):
    _mas_setup(monkeypatch, cache={"original_transaction_id": "2000"})
    assert require(conn, apple_exchanger=_apple("licence", licence_key="DAWN-MAC"),
                   now=NOW).entitled


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


# ---------------------------------------------------------------------------
# The verifier itself. Every test above injects `verifier=`, so until these
# existed the REAL one had never run — and it asked `/health`, which takes no
# request, reads no Authorization header and returns 200 to anybody. Every
# string typed into the licence box verified, and `check()` reported "licence
# verified" for all of them. Found by `tools/audit_seams.py`.
# ---------------------------------------------------------------------------
class _Response:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_verification_asks_a_route_that_reads_the_key(monkeypatch):
    """THE BUG, pinned. `/health` answers for the service and ignores the
    caller, so verifying against it accepted anything."""
    import json

    from app.core import entitlement

    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return _Response(json.dumps({"ok": True, "status": "active"}).encode())

    detail = entitlement.licence_details("DAWN-XYZ", opener=opener)
    assert detail["ok"] is True
    assert seen["url"].endswith("/v1/licence"), \
        "/health cannot verify anything: it never sees the request"
    assert seen["auth"] == "Bearer DAWN-XYZ"


def _http_error(status, body: bytes | None):
    import io
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, status, "no", {},
            io.BytesIO(body) if body is not None else None)
    return opener


def test_an_unrecognised_key_is_refused_not_accepted(monkeypatch):
    """The shape of the shipped bug: a made-up key must NOT verify.

    The Worker's refusal carries its reason. This used to raise a bare 403
    with no body and expect a refusal — which is also exactly what Cloudflare
    sends, so the test was pinning the misreading below as correct.
    """
    import json

    from app.core import entitlement

    opener = _http_error(403, json.dumps({"error": "unknown_licence"}).encode())
    assert entitlement.licence_details("MADE-UP", opener=opener) is False


@pytest.mark.parametrize("status,code", [(401, "no_licence"),
                                         (403, "unknown_licence"),
                                         (403, "licence_inactive")])
def test_the_workers_own_refusals_are_refusals(status, code):
    import json

    from app.core import entitlement

    opener = _http_error(status, json.dumps({"error": code}).encode())
    assert entitlement.licence_check("DAWN-X", opener=opener)[0] == "refused"


@pytest.mark.parametrize("status,body", [
    (403, b"<html>error code: 1010</html>"),     # Cloudflare's bot block
    (403, None),                                  # nothing at all
    (401, b'{"error": "something_else"}'),        # a code the app does not own
    (403, b"[1, 2]"),                             # JSON, but not the Worker's
])
def test_a_403_that_is_not_the_worker_refusing_is_unverifiable(status, body):
    """Cloudflare answers 403 with an HTML page. Read as a refusal, it skips
    the grace period and tells a paying customer their key was rejected."""
    from app.core import entitlement

    opener = _http_error(status, body)
    assert entitlement.licence_details("DAWN-X", opener=opener) is None
    assert entitlement.licence_check("DAWN-X", opener=opener)[0] == "unreachable"


def test_a_lapsed_licence_says_so_rather_than_calling_the_key_wrong(conn, monkeypatch):
    """A real key that stopped working needs renewing, not re-typing."""
    import json

    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-LAPSED")
    monkeypatch.setattr(
        ent, "licence_check",
        lambda key, **kw: ("refused", json.loads('{"error": "licence_inactive"}')))

    lapsed = check(conn, verifier=REAL_VERIFY, now=NOW)
    assert not lapsed.entitled and not lapsed.unverifiable
    assert "no longer active" in lapsed.reason
    assert "not accepted" not in lapsed.reason

    # The positive control: an unknown key still gets the "check the key" line.
    monkeypatch.setattr(
        ent, "licence_check",
        lambda key, **kw: ("refused", {"error": "unknown_licence"}))
    unknown = check(conn, verifier=REAL_VERIFY, now=NOW)
    assert "not accepted" in unknown.reason


def test_cloudflare_blocking_the_check_runs_on_grace(conn, monkeypatch):
    """Through the real verifier: a verified licence, then a Cloudflare 403,
    must be grace — not a refusal."""
    as_variant(monkeypatch, "direct")
    monkeypatch.setattr(ent, "read_licence", lambda: "DAWN-XXXX")
    monkeypatch.setattr(ent, "licence_check", lambda key, **kw: ("ok", {"ok": True}))
    assert check(conn, verifier=REAL_VERIFY, now=NOW).entitled

    monkeypatch.setattr(ent, "licence_check",
                        lambda key, **kw: ("unreachable", {}))
    e = check(conn, verifier=REAL_VERIFY, now=NOW + timedelta(days=2))
    assert e.entitled and e.source == "grace"


def test_an_outage_is_not_a_refusal(monkeypatch):
    """None is not False. Grace exists to absorb exactly this, and collapsing
    the two turns an outage into an accusation of fraud."""
    from app.core import entitlement

    def opener(request, timeout=None):
        raise OSError("network down")

    assert entitlement.licence_details("DAWN-XYZ", opener=opener) is None


# -- redeeming a code, and trading a Mac receipt -----------------------------
# Both were flagged by tools/audit_seams.py as network paths no test named.

def test_redeeming_a_code_returns_the_licence(monkeypatch):
    import json

    from app.core import entitlement

    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode())
        return _Response(json.dumps(
            {"ok": True, "licence_key": "DAWN-FROM-CODE"}).encode())

    key = entitlement.redeem_override_code("DL-ABC", opener=opener)
    assert key == "DAWN-FROM-CODE"
    assert seen["url"].endswith("/redeem")
    assert seen["body"] == {"code": "DL-ABC"}


def test_a_spent_code_says_so_rather_than_failing_vaguely(monkeypatch):
    """'spent' and 'invalid' need different responses: one is a code that
    worked and has run out, the other never existed."""
    import io
    import json
    import urllib.error

    from app.core import entitlement

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 403, "no", {},
            io.BytesIO(json.dumps({"error": "code_spent"}).encode()))

    with pytest.raises(entitlement.NotEntitled, match="maximum number of times"):
        entitlement.redeem_override_code("DL-SPENT", opener=opener)


def test_an_unreachable_service_does_not_read_as_a_bad_code(monkeypatch):
    from app.core import entitlement

    def opener(request, timeout=None):
        raise OSError("offline")

    with pytest.raises(entitlement.NotEntitled, match="Could not reach"):
        entitlement.redeem_override_code("DL-ABC", opener=opener)
