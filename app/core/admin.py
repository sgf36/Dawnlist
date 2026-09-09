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

#: What a code hands out. `trial` is the server's default on purpose: the safe
#: default for a credential given to a stranger is the smallest allowance.
PLANS = ("trial", "standard", "pro")

#: Not 1. See the class docstring — this is the Wren scar.
REVIEW_USES = 25


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
