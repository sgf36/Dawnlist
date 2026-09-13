"""Every outbound HTTP request Dawnlist makes, built in one place.

WHY THIS EXISTS, AND WHY IT IS NOT JUST A CONSTANT
--------------------------------------------------
urllib sends `Python-urllib/3.x` unless told otherwise, and Cloudflare refuses
that agent outright with **error 1010 and an HTTP 403**. On 2026-09-08 that
meant every request from every shipped build failed, with a message naming
neither Dawnlist nor Cloudflare.

It was fixed by adding a header at the call site. That fix did not hold, and
could not have:

  * `managed.py` was fixed on the day;
  * `theirstack.py` was found still sending the default on 2026-09-09;
  * `entitlement.py` was found sending the default the same afternoon — in
    THREE places, and each one is a whole feature:

        /v1/licence  every Windows licence check
        /v1/apple    the Mac App Store receipt exchange
        /redeem      every override code, including App Review's

  * `admin.py`, written that morning, never had it at all.

A 403 is not a neutral failure here. `licence_details` reads 401 and 403 as
"the server said no", and `check()` treats a refusal as FINAL — checked before
the grace period, deliberately, so a revoked licence cannot keep working for a
fortnight. So a paying customer would have been told, in plain words, that
their key was not accepted; a reviewer's code would have been refused; and the
Mac build would have reached no feed at all.

None of it could be seen from inside the project. The suite injects the
transport, so the real request was never made; the manual checks used `curl`,
whose agent is not blocked, so they passed while no real client could connect.

Hence a FUNCTION, not a constant. A constant has to be remembered at every new
call site, and the record above is four call sites forgetting it in two days.
`tests/test_http_client.py` makes forgetting a test failure: nothing in `app/`
may construct a `urllib.request.Request` except this module.
"""
from __future__ import annotations

import sys
import urllib.request

from app.version import marketing_version


def use_system_trust(platform: str = sys.platform) -> bool:
    """Verify HTTPS through the operating system's trust store, on macOS.

    WITHOUT THIS NO HTTPS REQUEST FROM THE MAC APP COULD SUCCEED. The
    python.org macOS interpreter that PyInstaller bundles has its own OpenSSL
    and no certificate store, and a frozen bundle has no certifi file, so
    urllib fails certificate verification immediately. Measured on the cloud
    Mac against build 173 on 2026-09-13: the /v1/apple exchange failed in under
    a second and the bundle contained no .pem file at all, so a sandbox
    purchase Apple had already completed could never become a licence. The
    failure is documented upstream (pyinstaller/pyinstaller#7229).

    `truststore.inject_into_ssl()` makes every `ssl` context verify through the
    macOS Security framework, which is what Safari trusts, and ships no
    certificate file that could go stale. It is meant for applications rather
    than libraries, which is exactly what this is.

    macOS only. Windows Python already reads the Windows certificate store, and
    the Store build works there; changing its trust path would be risk for no
    gain. Returns whether injection happened, so a test can tell.
    """
    if platform != "darwin":
        return False
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - never stop the app starting; the
        # request that then fails reports its own error.
        return False
    return True


# At import: every module that makes a request imports this one for
# `build_request`, so no request can be built before trust is in place.
use_system_trust()

#: Identifies the client to anything that logs or filters by agent. Never the
#: urllib default — see the module docstring.
#:
#: THE VERSION IS READ, NOT TYPED. This said "Dawnlist/1.0" throughout 1.1.0,
#: because it was a second copy of a fact that already had one home in
#: `app/version.py` and nothing made the two move together. Harmless in itself
#: — it only labels outbound requests — but it is the same shape as the faults
#: that cost this project a week: a true statement left standing after the
#: thing it described moved. There is one version, and this reads it.
USER_AGENT = f"Dawnlist/{marketing_version()} (+https://dawnlist.spencerfields.com)"


def build_request(url: str, *, data: bytes | None = None,
                  method: str | None = None,
                  headers: dict[str, str] | None = None
                  ) -> urllib.request.Request:
    """A request that is already identified, whatever else the caller adds.

    `method` defaults to urllib's own rule — POST when there is a body, GET
    otherwise — so a caller that only has a body does not have to say so twice.
    """
    request = urllib.request.Request(
        url, data=data, method=method or ("POST" if data is not None else "GET"))
    request.add_header("User-Agent", USER_AGENT)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return request
