"""The Mac App Store receipt, read as opaque bytes for one fallback.

WHAT IT IS FOR NOW
------------------
The Worker confirms a subscription from the StoreKit ORIGINAL TRANSACTION ID.
The receipt is sent only when no id was ever kept on this Mac — a customer who
subscribed through the build that was in App Review, which recorded none — and
the Worker's reply carries the id used from then on. See
`entitlement.exchange_and_cache`.

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
asks Apple and decides. That is the same boundary the feed already uses: the
client asks, the server rules.

NO EXIT 173
-----------
This module carried `exit_code_for_missing_receipt`, which nothing called,
beside a paragraph saying Apple requires every Mac App Store build to exit 173
without a receipt. That status belongs to apps that validate the receipt ON THE
DEVICE: it asks macOS to fetch one so the local check can run. Dawnlist checks
nothing locally, so quitting for a missing file would close the app for the
sake of a check it never makes. The helper and the claim are gone rather than
wired.
"""
from __future__ import annotations

import sys
from pathlib import Path

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
