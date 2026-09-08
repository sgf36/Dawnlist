"""The Mac App Store receipt, and turning it into an entitlement.

WHY THIS IS NOT A LICENCE KEY, WHICH MATTERS LEGALLY AND NOT JUST SEMANTICALLY
-----------------------------------------------------------------------------
Apple's guideline 3.1.1 names licence keys as a mechanism an app may not use to
unlock functionality. What it prohibits is the USER supplying a credential they
obtained outside Apple's commerce.

This is the opposite direction. The receipt is written into the app bundle by
the App Store at install and at purchase; the user never sees it, never types
it, and cannot obtain one any other way. Exchanging it for a session token is
what every server-backed subscription does, and the entitlement originates with
Apple throughout.

The distinction is where the entitlement COMES FROM, not what the token looks
like once it exists. Do not let the two paths converge: a MAS build must never
accept a key a person can paste, and `build_provider` enforces that separately.

WHY THE APP DOES NOT VALIDATE THE RECEIPT ITSELF
------------------------------------------------
The receipt is a PKCS#7 signed ASN.1 structure. Validating it means checking
Apple's certificate chain, the signature, the bundle id and the device hash —
and a validator that is subtly wrong is worse than none, because it fails open
and looks fine.

More importantly it is CLIENT-SIDE, so a determined user can patch it out. The
app therefore treats the receipt as opaque bytes and forwards them; the Worker
validates against Apple's App Store Server API and decides. That is the same
boundary the feed already uses: the client asks, the server rules.

WHAT APPLE REQUIRES OF A MAS BUILD REGARDLESS OF SUBSCRIPTIONS
--------------------------------------------------------------
A Mac App Store application must exit with status 173 when its receipt is
missing or invalid. macOS interprets that specific code as "fetch a receipt for
this app" and re-launches it. An app that starts normally without a receipt is
a copy anyone can run, and Apple rejects builds that do not do this.

`exit_code_for_missing_receipt` exists so that behaviour is stated in one place
and tested, rather than being a magic number somebody later mistakes for a bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: macOS reads this exact status as "this app needs a receipt". Any other
#: non-zero code is just a crash, and the app will not be re-launched.
MISSING_RECEIPT_EXIT = 173

#: Where the App Store places the receipt inside a .app bundle.
RECEIPT_RELATIVE = "Contents/_MASReceipt/receipt"


def bundle_root() -> Path | None:
    """The .app directory, when running from one.

    PyInstaller puts the executable in `Contents/MacOS/`, so the bundle is two
    levels up. Returns None off macOS, and when running from source, so callers
    can tell "no receipt" from "not a bundled Mac build" — which need opposite
    responses and would otherwise look identical.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def receipt_path() -> Path | None:
    root = bundle_root()
    return (root / RECEIPT_RELATIVE) if root else None


def read_receipt() -> bytes | None:
    """The raw receipt, or None when there is not one.

    Deliberately returns BYTES and does not look inside. The app has no opinion
    about what a valid receipt contains; that is the Worker's job, and giving
    the client an opinion is how a client-side check gets patched out.
    """
    path = receipt_path()
    if path is None or not path.exists():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None


def exit_code_for_missing_receipt() -> int:
    """173, stated once so it is not mistaken for an arbitrary error code.

    Apple requires a Mac App Store build to exit with this when the receipt is
    absent or invalid. macOS then fetches one and re-launches. Returning 1
    here, or raising, would leave a store customer with an app that closes
    itself and never explains why.
    """
    return MISSING_RECEIPT_EXIT
