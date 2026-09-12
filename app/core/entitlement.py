"""Is this copy of Dawnlist paid for?

**Dawnlist is a $79/month SUBSCRIPTION**, and where it is bought depends
entirely on which store's rules apply. Decided by Spencer on 2026-09-08; this
supersedes both the build handoff's Part 7 and the one-time-purchase model
that stood here until that date.

  WINDOWS — BOTH THE MICROSOFT STORE AND DIRECT DOWNLOAD — NEEDS A KEY.
  Microsoft permits third-party commerce for non-game PC apps (Store Policies
  10.8.1 and 10.8.6), so both Windows channels sell through Paddle and both
  ask for the same licence key. The Store listing is FREE, which means the
  key is the only thing standing between a download and the product.

  MAC APP STORE SELLS THE SUBSCRIPTION ITSELF, AND THERE IS NO MAC DIRECT
  DOWNLOAD.
  Apple's guideline 3.1.1 forbids licence keys outright, so a MAS build must
  never accept one. Entitlement comes from the StoreKit transaction, which
  the Worker confirms with Apple and exchanges for a licence the user never
  sees or types — so it originates with Apple throughout.

WHAT CHANGED, AND WHY IT MATTERS
--------------------------------
Store builds used to be entitled by POSSESSION, on the reasoning that a store
does not hand the binary to someone who has not paid. That reasoning holds for
a paid-upfront app and fails completely for a subscription behind a free
listing: it would have given every Microsoft Store customer the entire product
for nothing. Do not reintroduce it.

WHAT IS GATED
-------------
The morning run, and only the run. Onboarding, the board and every screen stay
open even on an unlicensed direct copy — because someone who has paid and is
mid-way through re-entering their key should still be able to see their own
tracker. Locking a person out of their own data to enforce a payment is a
punishment, not a gate.

WHERE THE GATE LIVES
--------------------
In `morning_run`, beside the calibration check, at the only door into a run.
Not in the UI: a gate enforced in a screen is one a scheduled run walks
straight past.

FAILING CLOSED WITHOUT STRANDING A PAYING USER
----------------------------------------------
A licence check can fail because someone has not paid, or because the network
was down for thirty seconds. Treating those alike blocks a paying customer over
someone else's outage. So a successful check is cached and honoured for a grace
period, and a refusal says WHICH of the two happened.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.build_variant import variant
from app.core.credentials import KeyringUnavailable
from app.i18n import tr

#: How long a verified licence is honoured without re-checking. Long enough to
#: cover an outage or a fortnight offline; short enough that a refunded licence
#: stops working in a reasonable time.
GRACE_DAYS = 14

VERIFIED_AT = "entitlement_verified_at"
VERIFIED_SOURCE = "entitlement_verified_source"
#: A SHA-256 of the key the stamp was earned by. A hash, because the settings
#: database is a plain file and the key itself belongs in the credential store.
VERIFIED_KEY = "entitlement_verified_key"

#: How far ahead of this machine's clock a stamp may sit and still count.
#: Clocks disagree by hours across zones and daylight saving; a stamp days in
#: the future came from a wrong clock or a hand edit, and would never age out.
FUTURE_TOLERANCE = timedelta(days=1)

#: The Worker. One constant so the endpoints cannot drift apart.
WORKER_BASE = "https://dawnlist-feed-worker.sgf36.workers.dev"

LICENCE_SERVICE = "dawnlist-licence"
LICENCE_ACCOUNT = "key"


class NotEntitled(RuntimeError):
    """Raised when a run is attempted on an unlicensed direct-download copy."""


@dataclass(frozen=True)
class Entitlement:
    entitled: bool
    #: 'store', 'mas', 'licence', 'grace', or '' when not entitled.
    source: str
    reason: str
    #: True when the licence could not be CHECKED, as opposed to checked and
    #: refused. The user can act on the difference; conflating them turns an
    #: outage into an accusation.
    unverifiable: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fingerprint(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def _stamp(conn: sqlite3.Connection, key: str, source: str,
           now: datetime) -> None:
    _set(conn, VERIFIED_AT, now.isoformat(timespec="seconds"))
    _set(conn, VERIFIED_SOURCE, source)
    _set(conn, VERIFIED_KEY, _fingerprint(key))


def clear_verification(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key IN (?, ?, ?)",
                 (VERIFIED_AT, VERIFIED_SOURCE, VERIFIED_KEY))
    conn.commit()


def _grace_elapsed(conn: sqlite3.Connection, key: str | None,
                   now: datetime) -> int | None:
    """Whole days since `key` last verified, or None when grace does not apply.

    `key` is None only when the credential store cannot be read, so there is
    nothing to compare.
    """
    verified_at = _parse(_get(conn, VERIFIED_AT))
    if verified_at is None or verified_at - now > FUTURE_TOLERANCE:
        return None
    # The stamp belongs to the key that earned it. Without this, any string
    # pasted into the box inherited a real key's stamp and ran through an
    # outage for a fortnight. A stamp written before fingerprints existed has
    # none, and earns grace again at its next successful check.
    if key is not None and _get(conn, VERIFIED_KEY) != _fingerprint(key):
        return None
    age = now - verified_at
    if age >= timedelta(days=GRACE_DAYS):
        return None
    return max(0, age.days)


def read_licence() -> str | None:
    """The stored licence, None when there is none; raises `KeyringUnavailable`
    when the credential store itself cannot be read."""
    from app.core import credentials
    return credentials.read(LICENCE_SERVICE, LICENCE_ACCOUNT)


def stored_licence() -> str | None:
    """The stored licence, or None for BOTH "no licence" and "no store".

    For callers that only display or forward a key. `check()` must tell the
    two apart and reads through `read_licence` instead.
    """
    try:
        return read_licence()
    except KeyringUnavailable:
        return None


#: The Worker's own words for "this licence is no good". ONLY these refuse.
#: Cloudflare answers 403 too — error 1010, a WAF rule, an account-level block
#: — with an HTML body that says nothing about the licence, and reading that
#: status as a refusal skips the grace period and tells a paying customer
#: their key was not accepted.
REFUSAL_CODES = frozenset({"unknown_licence", "licence_inactive", "no_licence"})


@dataclass(frozen=True)
class Refused:
    """A refusal that remembers WHY, and is falsy like the `False` it replaces.

    A lapsed subscription and a mistyped key need different instructions, and
    `False` could only say "no".
    """

    code: str = ""

    def __bool__(self) -> bool:
        return False


def licence_check(key: str, *, opener=None) -> tuple[str, dict]:
    """`("ok", body)`, `("refused", body)` or `("unreachable", body)`.

    The body is the Worker's JSON — its account of the licence on 200, its
    error on a refusal — or `{}` when nothing readable came back.
    """
    import json
    import urllib.error
    import urllib.request

    from app.core.http import build_request

    # Built through `build_request` so the User-Agent cannot be forgotten.
    # It was forgotten here, and Cloudflare's error 1010 answered 403 — which
    # was then read as the server refusing the licence.
    request = build_request(WORKER_BASE.rstrip("/") + "/v1/licence",
                            headers={"authorization": f"Bearer {key}"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=15) as response:
            return "ok", json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = _json_body(exc)
        if exc.code in (401, 403) and body.get("error") in REFUSAL_CODES:
            return "refused", body
        return "unreachable", body
    except Exception:  # noqa: BLE001
        return "unreachable", {}


def _json_body(exc) -> dict:
    import json
    try:
        loaded = json.loads(exc.read().decode())
    except Exception:  # noqa: BLE001 - HTML, empty or absent: not the Worker
        return {}
    return loaded if isinstance(loaded, dict) else {}


def licence_details(key: str, *, opener=None):
    """The server's own account of this licence.

    A dict on 200, ``False`` when the server refuses it, ``None`` when the
    server could not be reached. Three outcomes, because the caller treats
    them differently, and collapsing the last two turns an outage into an
    accusation.

    IT ASKS `/v1/licence`, NOT `/health`, AND THAT DIFFERENCE WAS THE BUG.
    `/health` reports on the SERVICE: it takes no request, never reads the
    Authorization header, and returns 200 to anybody. Verifying against it
    meant EVERY string typed into the licence box verified, and `check()`
    below then reported "licence verified" for all of them. The metered feed
    routes still refused, so nothing paid was handed over — but the gate was
    not a gate, and it told the person the opposite of the truth.

    It survived because every test injects `verifier=`, so this function was
    never the thing under test.
    """
    outcome, body = licence_check(key, opener=opener)
    if outcome == "ok":
        return body
    return False if outcome == "refused" else None


def verify_against_worker(key: str) -> bool | Refused | None:
    """True, a `Refused`, or None for 'could not tell'.

    None is NOT a refusal. It is what the grace period exists to absorb.
    """
    outcome, body = licence_check(key)
    if outcome == "ok":
        return True if body.get("ok") else Refused()
    if outcome == "refused":
        return Refused(str(body.get("error") or ""))
    return None


def check(conn: sqlite3.Connection, *, verifier=None,
          now: datetime | None = None, apple_exchanger=None) -> Entitlement:
    now = now or _now()
    build = variant()

    # A build with no variant flag, or two, was packaged wrong. Treating it as
    # Windows would unlock what may be a Mac build with a key, which guideline
    # 3.1.1 forbids; treating it as a Mac would ask a Windows customer for an
    # Apple subscription. So it refuses, and says the COPY is broken rather
    # than that nobody paid.
    if build not in ("store", "direct", "mas"):
        return Entitlement(False, "", tr("entitlement.no_variant", variant=build))

    # --- Mac App Store: Apple's commerce, with the same three outcomes -----
    #
    # This said "entitled" without asking anything, leaving the feed to refuse
    # later — so a lapsed subscriber passed the door, got an empty morning and
    # no reason. Possession is not payment here either: the listing is free and
    # the subscription is bought inside it.
    if build == "mas":
        return _check_mac(conn, now, apple_exchanger or exchange_and_cache)

    # --- Windows, BOTH the Store and direct download: a Paddle licence ----
    #
    # THE MICROSOFT STORE BUILD IS NOT ENTITLED BY POSSESSION, and used to be.
    # That was a hole, not a simplification: the Store listing is free, so
    # every Store customer would have received the whole subscription for
    # nothing, forever, with no payment anywhere in the loop.
    #
    # Microsoft permits third-party commerce for non-game PC apps (Store
    # Policies 10.8.1 and 10.8.6), subject only to declaring it in Partner
    # Center — which is done. So Windows sells through Paddle on both
    # channels, and the Store build asks for the same key the direct build
    # does. `app/ui/settings.py` already shows the licence panel on `store`
    # for exactly this reason; this is the half that was missing.
    #
    # Do not "restore" the possession shortcut for `store`. Apple is the store
    # that forbids keys; Microsoft is not, and applying Apple's rule to both
    # by assumption is what produced the hole.
    try:
        key = read_licence()
    except KeyringUnavailable:
        # NOT a refusal and not "no licence": nothing was asked, and the key
        # may be perfectly good. Grace goes by the stamp's age alone, because
        # no fingerprint can be compared — and nothing can be gained by it,
        # since a key the store will not hand over reaches no feed either.
        elapsed = _grace_elapsed(conn, None, now)
        if elapsed is not None:
            return Entitlement(
                True, "grace",
                f"the credential store could not be read; last verified "
                f"{elapsed} day(s) ago, running on grace for up to "
                f"{GRACE_DAYS - elapsed} more")
        return Entitlement(False, "", tr("entitlement.keyring_unavailable"),
                           unverifiable=True)
    if not key:
        return Entitlement(
            False, "",
            "No licence key found. Enter the key from your purchase email, or "
            "an override code if you were given one. Your board and your brief "
            "stay open either way.")

    verdict = (verifier or verify_against_worker)(key)

    if verdict is True:
        _stamp(conn, key, "licence", now)
        return Entitlement(True, "licence", "licence verified")

    # A REFUSAL is final and is checked BEFORE the grace period. Grace exists
    # to absorb an outage, never a revoked licence — otherwise a refund keeps
    # working for a fortnight, which is the grace period paying for fraud.
    if verdict is not None:
        if getattr(verdict, "code", "") == "licence_inactive":
            # The key is real and has stopped working. "Not accepted" would
            # send this person to re-type a key that was never wrong.
            return Entitlement(False, "", tr("entitlement.licence_inactive"))
        return Entitlement(
            False, "",
            "This licence key was not accepted. If you have just bought "
            "Dawnlist, check the key from your purchase email.")

    # verdict is None: we reached nothing conclusive. THIS is what grace is for.
    elapsed = _grace_elapsed(conn, key, now)
    if elapsed is not None:
        return Entitlement(
            True, "grace",
            f"last verified {elapsed} day(s) ago; running on a cached licence "
            f"for up to {GRACE_DAYS - elapsed} more")

    return Entitlement(
        False, "",
        "Could not reach the licence service, and this copy has not been "
        "verified recently. Check your connection and try again — nothing "
        "has been cancelled.",
        unverifiable=True)


def _check_mac(conn: sqlite3.Connection, now: datetime,
               exchanger) -> Entitlement:
    """Apple's answer, and the same grace a Windows licence gets.

    Licence, refused or unreachable — each with its own words, because "your
    subscription lapsed" and "the network is down" need opposite actions.
    """
    # AN ACCESS-CODE GRANT FIRST: the one route besides a purchase that 3.1.1
    # allows, because nothing is bought. A PURCHASED licence in the keyring —
    # left by a Windows or direct build under the same user — is never
    # honoured, and falls through to Apple.
    try:
        granted = read_licence()
    except KeyringUnavailable:
        granted = None
    if granted:
        outcome, body = licence_check(granted)
        if (outcome == "ok" and body.get("ok") and body.get("granted_by_code")
                and not body.get("purchased")):
            _stamp(conn, granted, "grant", now)
            return Entitlement(True, "licence", "access code verified")
        if outcome == "unreachable" and _get(conn, VERIFIED_SOURCE) == "grant":
            elapsed = _grace_elapsed(conn, granted, now)
            if elapsed is not None:
                return Entitlement(
                    True, "grace",
                    f"access code last verified {elapsed} day(s) ago; running "
                    f"on grace for up to {GRACE_DAYS - elapsed} more")

    try:
        cache = apple_cache()
    except KeyringUnavailable:
        elapsed = _grace_elapsed(conn, None, now)
        if elapsed is not None:
            return Entitlement(
                True, "grace",
                f"the credential store could not be read; subscription last "
                f"confirmed {elapsed} day(s) ago")
        return Entitlement(False, "", tr("entitlement.keyring_unavailable"),
                           unverifiable=True)

    result = exchanger(cache.get("original_transaction_id"))
    if result.outcome == "licence":
        _stamp(conn, result.licence_key, "mas", now)
        return Entitlement(True, "mas", "App Store subscription confirmed")
    # Checked BEFORE grace, exactly as on Windows: grace absorbs an outage,
    # never Apple saying the subscription lapsed or was refunded.
    if result.outcome == "refused":
        return Entitlement(False, "", tr("entitlement.mac_lapsed"))
    if result.outcome == "none":
        return Entitlement(False, "", tr("entitlement.mac_not_subscribed"))

    # Measured against the licence Apple last issued, so the stamp cannot
    # carry any other key through the outage.
    kept = cache.get("licence_key")
    elapsed = _grace_elapsed(conn, kept, now) if kept else None
    if elapsed is not None:
        return Entitlement(
            True, "grace",
            f"subscription last confirmed {elapsed} day(s) ago; running on a "
            f"cached licence for up to {GRACE_DAYS - elapsed} more")
    return Entitlement(False, "", tr("entitlement.mac_unreachable"),
                       unverifiable=True)


def require(conn: sqlite3.Connection, *, verifier=None,
            now: datetime | None = None, apple_exchanger=None) -> Entitlement:
    """Raise unless entitled. Called at the door into a run."""
    result = check(conn, verifier=verifier, now=now,
                   apple_exchanger=apple_exchanger)
    if not result.entitled:
        raise NotEntitled(result.reason)
    return result


def store_licence(key: str, *, conn: sqlite3.Connection | None = None) -> None:
    """Save a licence from a purchase or an override code.

    One slot for both, because an override code produces a real licence — so
    there is no second code path to keep in step with the first.

    Deliberately does NOT mark it verified: the next check verifies it
    properly, so a mistyped key fails at the door rather than being trusted
    because it was the most recent thing written.

    AND IT CLEARS THE OLD STAMP. The fingerprint stops a DIFFERENT key riding
    it, but not the same key saved again: a licence refunded and then pasted
    back during an outage would match its own old stamp and run on grace for
    the rest of the fortnight.
    """
    from app.core import credentials
    credentials.write(LICENCE_SERVICE, LICENCE_ACCOUNT, key.strip())
    _forget_verification(conn)


def _forget_verification(conn: sqlite3.Connection | None) -> None:
    """Clear the stamp in `conn`, or in the default database when none is given.

    The panels that save a key have no connection of their own. Failure is
    swallowed: the fingerprint still stops any other key using the stamp, and
    a save that raised here would lose a licence the user just paid for.
    """
    try:
        if conn is not None:
            clear_verification(conn)
            return
        from contextlib import closing

        from app.core.db import connect, default_db_path
        path = default_db_path()
        if not path.exists():
            return
        with closing(connect(path)) as own:
            clear_verification(own)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# The Mac App Store subscription
# ---------------------------------------------------------------------------

#: Where a Mac build keeps what the Worker last said about the subscription:
#: in the credential store beside the licence slot, because the licence it
#: holds is a bearer credential for the feed.
APPLE_ACCOUNT = "apple"

#: How long one exchange stands in for another. Long enough to cover the door
#: into a run and the feed built straight after it; far shorter than anything
#: that could outlive a refund.
APPLE_FRESH = timedelta(minutes=10)

#: The Worker's words for APPLE having refused. Everything else — the Worker's
#: own misconfiguration, Apple being down, a Cloudflare block — means the
#: subscription could not be asked about, which is what grace absorbs.
APPLE_REFUSALS = frozenset({"not_subscribed", "unknown_transaction",
                            "wrong_bundle", "wrong_product"})


@dataclass(frozen=True)
class AppleExchange:
    """What `/v1/apple` said: `licence`, `refused` or `unreachable`, or
    `none` when there was nothing to ask with."""

    outcome: str
    licence_key: str | None = None
    expires_at: str | None = None
    status: str = ""
    error: str = ""
    original_transaction_id: str | None = None
    #: False when the answer could not be written to the credential store.
    saved: bool = True


def exchange_apple(*, original_transaction_id: str | None = None,
                   receipt: bytes | None = None, base: str | None = None,
                   opener=None) -> AppleExchange:
    """Ask the Worker whether Apple holds an active subscription.

    THE TRANSACTION ID IS THE CONTRACT. It comes from StoreKit and the Worker
    looks it up with the App Store Server API. The client used to post the
    receipt to this route, which did not exist, and read the 404 as "not
    subscribed". A receipt is still accepted, for one case: a customer who
    subscribed through a build that never kept the id.

    Three outcomes, never two. A refusal is Apple's; anything else is a fact
    about the network or the Worker, and must not tell a subscriber they have
    not paid.

    The licence this returns is NOT a key the user could have typed, which is
    what keeps it the right side of guideline 3.1.1.
    """
    import base64
    import json
    import urllib.error
    import urllib.request

    from app.core.http import build_request

    if original_transaction_id:
        payload = {"originalTransactionId": str(original_transaction_id)}
    else:
        payload = {"receipt": base64.b64encode(receipt or b"").decode()}
    request = build_request((base or WORKER_BASE).rstrip("/") + "/v1/apple",
                            data=json.dumps(payload).encode(), method="POST",
                            headers={"Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = _json_body(exc)
        if exc.code == 403 and body.get("error") in APPLE_REFUSALS:
            return AppleExchange(
                "refused", status=str(body.get("status") or ""),
                error=str(body["error"]), expires_at=body.get("expires_at"),
                original_transaction_id=body.get("original_transaction_id"))
        return AppleExchange("unreachable",
                             error=str(body.get("error") or f"http_{exc.code}"))
    except Exception:  # noqa: BLE001 - an outage is not a refusal
        return AppleExchange("unreachable")

    if not isinstance(body, dict) or not body.get("licence_key"):
        return AppleExchange("unreachable", error="no_licence_in_reply")
    return AppleExchange(
        "licence", licence_key=body["licence_key"],
        expires_at=body.get("expires_at"), status=str(body.get("status") or ""),
        original_transaction_id=body.get("original_transaction_id"))


def apple_cache() -> dict:
    """What was last learned about the subscription. Raises `KeyringUnavailable`."""
    import json

    from app.core import credentials

    raw = credentials.read(LICENCE_SERVICE, APPLE_ACCOUNT)
    try:
        loaded = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_apple_cache(cache: dict) -> None:
    import json

    from app.core import credentials

    credentials.write(LICENCE_SERVICE, APPLE_ACCOUNT,
                      json.dumps(cache, sort_keys=True))


def _save_apple_cache(cache: dict) -> bool:
    try:
        write_apple_cache(cache)
    except KeyringUnavailable:
        return False
    return True


def exchange_and_cache(original_transaction_id: str | None = None, *,
                       opener=None, now: datetime | None = None) -> AppleExchange:
    """Exchange, and remember what came back.

    THE ID IS WRITTEN BEFORE ASKING. StoreKit hands it over only while it is
    delivering a transaction, so a purchase made during an outage has to be
    exchangeable tomorrow from what was kept today.

    A REFUSAL DROPS THE LICENCE and keeps the id, so a lapsed subscription is
    never run on from the cache, and a renewal can still be asked about.
    """
    from dataclasses import replace

    now = now or _now()
    try:
        cache, saved = apple_cache(), True
    except KeyringUnavailable:
        cache, saved = {}, False
    before = dict(cache)

    oid = original_transaction_id or cache.get("original_transaction_id")
    if oid and cache.get("original_transaction_id") != str(oid):
        # A different purchase — another Apple ID on this Mac — and the old
        # licence is no evidence about it.
        cache = {"original_transaction_id": str(oid)}
        saved = _save_apple_cache(cache) and saved
        before = dict(cache)

    if oid:
        result = exchange_apple(original_transaction_id=oid, opener=opener)
    else:
        from app.core.mac_receipt import read_receipt

        receipt = read_receipt()
        if receipt is None:
            return AppleExchange("none", saved=saved)
        result = exchange_apple(receipt=receipt, opener=opener)

    if result.original_transaction_id:
        cache["original_transaction_id"] = str(result.original_transaction_id)
    if result.outcome == "licence":
        cache.update(licence_key=result.licence_key,
                     expires_at=result.expires_at,
                     checked_at=now.isoformat(timespec="seconds"))
    elif result.outcome == "refused":
        for field in ("licence_key", "expires_at", "checked_at"):
            cache.pop(field, None)
    if cache != before:
        saved = _save_apple_cache(cache) and saved
    return replace(result, saved=saved)


def fresh_apple_licence(cache: dict, now: datetime | None = None) -> str | None:
    """The cached licence, if Apple confirmed it within `APPLE_FRESH`."""
    now = now or _now()
    checked = _parse(cache.get("checked_at"))
    key = cache.get("licence_key")
    if key and checked and timedelta(0) <= now - checked < APPLE_FRESH:
        return key
    return None


def redeem_override_code(code: str, *, opener=None) -> str:
    """Exchange an override code for a licence at the Worker.

    Returns the licence key, which the caller stores. Raises on refusal, with
    the server's own reason: 'spent' and 'invalid' need different responses.
    """
    import json
    import urllib.error
    import urllib.request

    from app.core.http import build_request

    request = build_request(
        WORKER_BASE.rstrip("/") + "/redeem",
        data=json.dumps({"code": code}).encode(),
        headers={"content-type": "application/json"},
        method="POST")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            payload = {"error": f"http_{exc.code}"}
        raise NotEntitled(_redeem_message(payload.get("error"))) from None
    except Exception as exc:  # noqa: BLE001
        raise NotEntitled(
            "Could not reach the licence service to redeem that code.") from exc

    if not payload.get("ok"):
        raise NotEntitled(_redeem_message(payload.get("error")))
    return payload["licence_key"]


def _redeem_message(error: str | None) -> str:
    return {
        "invalid_code": "That code was not recognised.",
        "expired_code": "That code has expired.",
        "code_spent": "That code has already been used the maximum number of times.",
        "too_many_attempts": "Too many attempts from this connection. Try again tomorrow.",
    }.get(error or "", "That code could not be redeemed.")
