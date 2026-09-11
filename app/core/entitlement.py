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
  never accept one. Entitlement comes from the App Store receipt, exchanged
  for a session token by the Worker in `build_provider` — see
  `app/core/mac_receipt.py` for why that is the right side of 3.1.1.

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
from app.i18n import tr

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
          now: datetime | None = None) -> Entitlement:
    now = now or _now()
    build = variant()

    # --- Mac App Store: Apple's commerce, enforced one layer down ---------
    #
    # A MAS build carries no pasted key at all (guideline 3.1.1), so there is
    # nothing here for this gate to check. The real gate is `build_provider`,
    # which trades the App Store receipt for a licence and refuses when Apple
    # reports no active subscription — so a copy without one reaches no feed.
    #
    # Possession is NOT proof of payment here either: the Mac App Store listing
    # is free to download and the subscription is bought inside it. This says
    # entitled only because the paying check lives at the feed.
    if build == "mas":
        return Entitlement(True, "mas", "subscribed through the Mac App Store")

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


def exchange_mac_receipt(receipt: bytes, *, base: str | None = None,
                         opener=None) -> str | None:
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

    `opener` exists so this can be TESTED. It is the whole Mac purchase path
    on the client side and nothing had ever executed a line of it: there was
    no seam to inject, so the suite could not reach it and `audit_seams.py`
    reported it as a network path no test names. Every other Worker call in
    this module already takes one; this was the omission, and it was the one
    function where the first real execution would be a customer paying money.
    """
    import base64
    import json
    import urllib.error
    import urllib.request

    url = (base or WORKER_BASE).rstrip("/") + "/v1/apple"
    body = json.dumps({"receipt": base64.b64encode(receipt).decode()}).encode()
    from app.core.http import build_request

    req = build_request(url, data=body, method="POST",
                        headers={"Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=30) as r:
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
