"""The admin console's client: issue, list and withdraw override codes.

The Worker already had all of this (`server/dawnlist-feed-worker/src/codes.js`)
and nothing in the app had ever called it. This is the missing half.

AUTHORISATION IS THE SERVER'S DECISION, RE-READ EVERY REQUEST. The role lives
in the `licence_roles` table, not inside the issued licence, so withdrawing an
administrator takes effect immediately — even on a machine already holding a
perfectly valid licence. Nothing here caches it, and nothing here should: a
cached "you are an admin" is a decision made by the wrong computer.

TWO RULES CARRIED OVER FROM WREN, both of which cost a release there:

  * A REVIEW CODE MUST NOT BE SINGLE-USE. A reviewer may test on several
    machines, or re-test after a rejection, and a spent code turns that into a
    failed review with no way back in. `REVIEW_USES` exists so that is a
    default rather than something remembered at the wrong moment.

  * A CODE WITH NO NOTE CANNOT BE AUDITED. The server refuses one
    (`note_required`), and so does this, before spending a round trip.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.core.entitlement import WORKER_BASE

#: The ladder, exactly as the server's CHECK constraint spells it. 'admin'
#: presumes 'managed', which presumes 'byo'.
ROLES = ("byo", "managed", "admin")

#: What a code hands out, spelt exactly as the Worker's PLANS
#: (server/dawnlist-feed-worker/src/plans.js). `trial` is the server's default
#: on purpose: the safe default for a credential given to a stranger is the
#: smallest allowance. This listed "pro", which the Worker has never had, so a
#: code issued as "pro" silently became a trial.
PLANS = ("trial", "standard", "global", "owner")

#: Not 1. See the class docstring — this is the Wren scar.
REVIEW_USES = 25

#: How long a code works when the issuer does not say otherwise. A reviewer's
#: or a friend's code that never expires is a standing grant nobody remembers
#: issuing.
DEFAULT_EXPIRY_DAYS = 30


def may_be_open_ended(role: str, plan: str) -> bool:
    """Only an administrator's own code, or the owner plan, may never expire."""
    return role == "admin" or plan == "owner"


def expiry_after(days: int, *, now=None) -> str | None:
    """`expires_at` for a code lasting `days`; None for 0, which means never.

    In the Worker's own shape, because it compares `expires_at` with
    `new Date().toISOString()` AS TEXT: a Python isoformat ending "+00:00"
    sorts wrongly against a string ending "Z".
    """
    if days <= 0:
        return None
    from datetime import datetime, timedelta, timezone

    when = (now or datetime.now(timezone.utc)) + timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class AdminError(RuntimeError):
    """A refusal worth showing the operator verbatim."""


def _request(key: str, path: str, *, body=None, method="GET", opener=None):
    from app.core.http import build_request

    url = WORKER_BASE.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    # Never a bare `urllib.request.Request`. Without the User-Agent this
    # module sent Cloudflare the agent it refuses with error 1010, and every
    # answer here would have been an unexplained 403 that reads as "this
    # licence is not an administrator".
    request = build_request(url, data=data, method=method, headers={
        "authorization": f"Bearer {key}",
        "content-type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            payload = {}
        raise AdminError(_message(exc.code, payload)) from None
    except Exception as exc:  # noqa: BLE001
        raise AdminError("Could not reach the licence service.") from exc


def _message(code: int, payload: dict) -> str:
    if payload.get("message"):
        return str(payload["message"])
    return {
        "no_licence": "No licence key on this machine.",
        "not_permitted": "This licence is not an administrator.",
        "note_required": "Say who the code is for; an unlabelled code cannot "
                         "be audited later.",
        "bad_json": "The server could not read that request.",
    }.get(payload.get("error", ""), f"The server refused that ({code}).")


def list_codes(key: str, *, opener=None) -> list[dict]:
    """Every code, with a live `uses` count.

    The count is computed from `redemptions` server-side rather than stored on
    the code, so it cannot drift out of step with reality — there is no second
    source of truth to disagree with.
    """
    return _request(key, "/admin/codes", opener=opener).get("codes", [])


def issue_code(key: str, *, note: str, role: str = "byo",
               plan: str = "trial", max_uses: int = 1,
               expires_at: str | None = None, opener=None) -> dict:
    """Mint one code. `note` is mandatory, here as well as on the server."""
    if not note.strip():
        raise AdminError("Say who this code is for; an unlabelled code cannot "
                         "be audited later.")
    if role not in ROLES:
        raise AdminError(f"Unknown role {role!r}.")
    if max_uses < 1:
        raise AdminError("A code has to be usable at least once.")
    return _request(key, "/admin/codes", method="POST", opener=opener, body={
        "note": note.strip(), "role": role, "plan": plan,
        "max_uses": max_uses, "expires_at": expires_at})


def revoke_code(key: str, code: str, *, opener=None) -> dict:
    """Withdraw a code. Takes effect on the next request anyone makes with it,
    because the server re-reads the table rather than trusting the licence."""
    return _request(key, "/admin/revoke", method="POST", opener=opener,
                    body={"code": code})


def is_admin(key: str, *, opener=None) -> bool:
    """Whether THIS licence may open the console.

    Asked of the server, never inferred locally: an app that decides its own
    privileges is deciding with the wrong computer, and a withdrawn admin would
    keep their console until they restarted.
    """
    from app.core.entitlement import licence_details

    detail = licence_details(key, opener=opener)
    return bool(detail) and detail is not False and detail.get("role") == "admin"


# ---------------------------------------------------------------------------
# Mac App Store offer codes
# ---------------------------------------------------------------------------
#
# These are NOT Dawnlist's own codes and nothing here can mint one. An offer
# code exists only once Apple has minted it, which needs an App Store Connect
# key that deliberately lives outside this application — `tools/asc_offer_codes.py`
# and `migrations/011-apple-offer-codes.sql` both say why.
#
# What the console does is the half that is safe to do from anywhere: see what
# is left, hand one to a named person, and take one out of circulation. That is
# also the half that has to work on Windows, because the person handing codes
# to Mac testers does not have a Mac.

def apple_codes(key: str, *, state: str = "all", opener=None) -> dict:
    """The offer-code ledger: `codes` and a per-batch summary in `batches`.

    `state` is 'all', 'free', 'assigned' or 'void'. Unknown values are treated
    as 'all' by the server rather than refused, because a filter nobody
    recognises should show everything rather than an empty screen that looks
    like there are no codes left.
    """
    return _request(key, f"/admin/apple-codes?state={state}", opener=opener)


def assign_apple_code(key: str, *, assigned_to: str, note: str = "",
                      batch: str = "", opener=None) -> dict:
    """Take the oldest unassigned code and record who it went to.

    The SERVER chooses which code, in one statement, so two consoles open at
    once cannot hand the same string to two people. Nothing here picks a code
    and then claims it — that shape is the bug.
    """
    assigned_to = (assigned_to or "").strip()
    if not assigned_to:
        # Refused before a round trip, as `issue_code` refuses an empty note:
        # a code nobody is recorded against cannot be accounted for later.
        raise AdminError("Say who this code is going to.")
    return _request(key, "/admin/apple-codes/assign", method="POST",
                    body={"assigned_to": assigned_to, "note": note,
                          "batch": batch}, opener=opener)


def void_apple_code(key: str, code: str, *, opener=None) -> dict:
    """Stop handing this code out from here.

    IT REMAINS REDEEMABLE AT APPLE. There is no API to withdraw a minted
    one-time code, so this is local bookkeeping and the caller must say so —
    the server returns `still_redeemable_at_apple` for exactly that reason. A
    screen that reported this as "revoked" would be telling its user the one
    thing that is not true.
    """
    return _request(key, "/admin/apple-codes/void", method="POST",
                    body={"code": (code or "").strip().upper()}, opener=opener)


def apple_subscribers(key: str, *, opener=None) -> dict:
    """Who redeemed, and whether they may be comped.

    NOT the same set as the offer-code ledger. A code can be handed out and
    never used; only a redemption produces a transaction, and only a
    transaction can be comped.

    `comp_eligible` is computed by the SERVER from the offer Apple recorded,
    and the console must use that answer rather than deriving its own. A button
    enabled by a second copy of the rule is a button that lies the day the two
    copies disagree.
    """
    return _request(key, "/admin/apple-subscribers", opener=opener)


def set_apple_comp(key: str, *, original_transaction_id: str, comp: bool,
                   opener=None) -> dict:
    """Keep this Mac user's licence alive past Apple's free period, or stop.

    Switching comp ON is refused by the server unless the subscription began
    with a comp offer — evidence from the transaction Apple signed, which
    nothing here can fabricate. Switching it OFF is never refused: withdrawing
    access must not be blocked by a gate that exists to control granting it.
    """
    return _request(key, "/admin/apple-comp", method="POST",
                    body={"original_transaction_id": str(original_transaction_id),
                          "comp": bool(comp)}, opener=opener)
