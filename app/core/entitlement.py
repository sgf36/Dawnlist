"""Is this copy of Dawnlist paid for?

**Dawnlist is a PAID APP.** Not free-with-an-unlock, not free-with-a-trial. You
buy it, then you have it. That decision (Spencer, 2026-09-06) supersedes the
build handoff's Part 7, which described a free download gated by a licence and
a store "Production Unlock" add-on. If you are reading the handoff, this file
is the newer decision.

That makes the entitlement question almost trivial, and deliberately so:

  STORE BUILDS ARE ENTITLED BY POSSESSION.
  The Microsoft Store and the Mac App Store do not hand the binary to someone
  who has not bought it. Re-asking the store whether the person holding the app
  is allowed to hold the app adds a network call, a failure mode, and a way to
  lock out a paying customer during an outage — in exchange for nothing. There
  is no add-on to read and no receipt to check, because there is no in-app
  purchase.

  DIRECT DOWNLOAD NEEDS A LICENCE KEY.
  Nothing stops a copied folder being run, so the direct build checks a licence
  issued by Paddle at purchase, or by an override code.

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

#: How long a verified licence is honoured without re-checking. Long enough to
#: cover an outage or a fortnight offline; short enough that a refunded licence
#: stops working in a reasonable time.
GRACE_DAYS = 14

VERIFIED_AT = "entitlement_verified_at"
VERIFIED_SOURCE = "entitlement_verified_source"

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


def stored_licence() -> str | None:
    try:
        import keyring
        return keyring.get_password(LICENCE_SERVICE, LICENCE_ACCOUNT)
    except Exception:  # noqa: BLE001 - a broken keyring is not a refusal
        return None


def verify_against_worker(key: str) -> bool | None:
    """True, False, or None for 'could not tell'.

    None is NOT False. It is what the grace period exists to absorb.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        "https://dawnlist-feed-worker.sgf36.workers.dev/health")
    request.add_header("authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        # 403 is the server saying no. Anything else is the network saying
        # nothing, which is a different fact.
        return False if exc.code == 403 else None
    except Exception:  # noqa: BLE001
        return None


def check(conn: sqlite3.Connection, *, verifier=None,
          now: datetime | None = None) -> Entitlement:
    now = now or _now()
    build = variant()

    # --- store builds: entitled by possession -----------------------------
    if build in ("store", "mas"):
        where = "the Microsoft Store" if build == "store" else "the Mac App Store"
        return Entitlement(True, build, f"purchased through {where}")

    # --- direct download: a licence key -----------------------------------
    key = stored_licence()
    if not key:
        return Entitlement(
            False, "",
            "No licence key found. Enter the key from your purchase email, or "
            "an override code if you were given one. Your board and your brief "
            "stay open either way.")

    verdict = (verifier or verify_against_worker)(key)

    if verdict is True:
        _set(conn, VERIFIED_AT, now.isoformat(timespec="seconds"))
        _set(conn, VERIFIED_SOURCE, "licence")
        return Entitlement(True, "licence", "licence verified")

    # A REFUSAL is final and is checked BEFORE the grace period. Grace exists
    # to absorb an outage, never a revoked licence — otherwise a refund keeps
    # working for a fortnight, which is the grace period paying for fraud.
    if verdict is False:
        return Entitlement(
            False, "",
            "This licence key was not accepted. If you have just bought "
            "Dawnlist, check the key from your purchase email.")

    # verdict is None: we reached nothing conclusive. THIS is what grace is for.
    verified_at = _parse(_get(conn, VERIFIED_AT))
    if verified_at and now - verified_at < timedelta(days=GRACE_DAYS):
        elapsed = (now - verified_at).days
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


def require(conn: sqlite3.Connection, *, verifier=None,
            now: datetime | None = None) -> Entitlement:
    """Raise unless entitled. Called at the door into a run."""
    result = check(conn, verifier=verifier, now=now)
    if not result.entitled:
        raise NotEntitled(result.reason)
    return result


def store_licence(key: str) -> None:
    """Save a licence from a purchase or an override code.

    One slot for both, because an override code produces a real licence — so
    there is no second code path to keep in step with the first.

    Deliberately does NOT mark it verified: the next check verifies it
    properly, so a mistyped key fails at the door rather than being trusted
    because it was the most recent thing written.
    """
    import keyring
    keyring.set_password(LICENCE_SERVICE, LICENCE_ACCOUNT, key.strip())


def exchange_mac_receipt(receipt: bytes, *, base: str | None = None) -> str | None:
    """Trade a Mac App Store receipt for a licence the Worker will honour.

    Returns the licence key, or None when Apple does not recognise an active
    subscription — which is a legitimate answer (lapsed, refunded, or a
    sandbox receipt against production) and not an error to hide.

    THE RECEIPT IS SENT AS OPAQUE BYTES. The app forms no view about what is
    inside it; the Worker validates against Apple and decides. A client-side
    check is one a determined user patches out, and one that is subtly wrong
    fails open while looking fine.

    The licence this returns is NOT a key the user could have typed. It is a
    session token derived from an Apple-issued receipt, and a MAS build has no
    route to obtain one any other way — which is what keeps this the right side
    of guideline 3.1.1.
    """
    import base64
    import json
    import urllib.error
    import urllib.request

    url = (base or WORKER_BASE).rstrip("/") + "/v1/apple"
    body = json.dumps({"receipt": base64.b64encode(receipt).decode()}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001 - an outage is not a refusal
        return None
    return payload.get("licence_key") or None


def redeem_override_code(code: str, *, opener=None) -> str:
    """Exchange an override code for a licence at the Worker.

    Returns the licence key, which the caller stores. Raises on refusal, with
    the server's own reason: 'spent' and 'invalid' need different responses.
    """
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        "https://dawnlist-feed-worker.sgf36.workers.dev/redeem",
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
