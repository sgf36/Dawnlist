"""App Store Connect, for Dawnlist. Auth and one call() everything goes through.

    from tools.asc import call, APP

WHY THIS FILE EXISTS SEPARATELY FROM wren/store
------------------------------------------------
The Wren scripts work and are the reference, but they are pinned to Wren's app
id and live in a different repository. Copying auth into each new script is how
a key rotation ends up half-applied, so Dawnlist gets one module and every
script imports it.

THE CREDENTIALS ARE NOT IN THIS REPOSITORY AND MUST NOT BE.
The .p8 lives outside the tree entirely, and `sgf36/Dawnlist` is PUBLIC — this
file said "private", which was true when it was written and stopped being true
without anything here noticing. The key opens every app on the account, not
just this one — Easy-Post, Wren and the mobile companion included — so it is
not a Dawnlist secret to hold in any case.

WHERE THE KEY IS, IS ALSO NOT IN THIS FILE. It used to be a literal path into
one developer's OneDrive, which made every script here unrunnable anywhere
else and published the shape of that person's filing on a public repository.
Set `ASC_KEY_PATH`, or store the path in Credential Manager under service
`dawnlist-asc`, account `key-path`.

A JWT IS MINTED PER CALL WITH iat BACKDATED SIXTY SECONDS. Apple rejects a
token whose iat is in the future by its own clock, and a laptop that is a few
seconds fast produces a 401 that reads exactly like a bad key. Backdating costs
nothing and removes the failure mode.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.request

KEY_ID = "4CU796U485"
ISSUER = "65aee88f-46c4-4daf-8238-5dc37263d06b"

#: Where to look for the .p8, in order. The environment variable wins so a
#: one-off run can point at a different key without touching the store.
KEY_ENV = "ASC_KEY_PATH"
KEY_SERVICE = "dawnlist-asc"
KEY_ACCOUNT = "key-path"


def key_path() -> pathlib.Path:
    """The .p8, or a refusal that says how to configure one.

    Resolved per call rather than at import, so that importing this module for
    its identifiers — which `asc_listing.py` and friends all do — never needs a
    key, and so a rotation takes effect without restarting anything.
    """
    configured = os.environ.get(KEY_ENV)
    if not configured:
        import keyring  # not needed when the environment already answers

        configured = keyring.get_password(KEY_SERVICE, KEY_ACCOUNT)
    if not configured:
        raise SystemExit(
            f"no App Store Connect key configured. Set {KEY_ENV} to the .p8 "
            f"path, or store it with:\n"
            f"  python -c \"import keyring; keyring.set_password("
            f"'{KEY_SERVICE}', '{KEY_ACCOUNT}', r'<path to "
            f"AuthKey_{KEY_ID}.p8>')\"")

    path = pathlib.Path(configured).expanduser()
    if not path.is_file():
        # Named, because a missing key and a wrong key both come back from
        # Apple as 401 and the message is identical.
        raise SystemExit(f"the configured App Store Connect key {path} does "
                         f"not exist")
    return path

#: Dawnlist in App Store Connect. Confirmed live 2026-09-08: the record already
#: existed with a MAC_OS 1.0 version in PREPARE_FOR_SUBMISSION.
APP = "6809836433"
BUNDLE = "com.spencerfields.dawnlist"

#: The App Store listing's primary locale, read from the record rather than
#: assumed. Localisation POSTs that use a locale the app does not declare are
#: rejected with a message that names neither the locale nor the field.
PRIMARY_LOCALE = "en-GB"

BASE = "https://api.appstoreconnect.apple.com"


def token() -> str:
    import jwt  # PyJWT is a tooling dependency, not one the app ships

    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER, "iat": now - 60, "exp": now + 1140,
         "aud": "appstoreconnect-v1"},
        key_path().read_text(),
        algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"})


def call(method: str, path: str, body=None, version: str = "v1"):
    """Returns (status, parsed_body). NEVER raises on an HTTP error.

    Apple puts the useful text in the error BODY, and urllib throws that away
    unless you read it off the exception. A script that lets HTTPError
    propagate reports '400 Bad Request' for a problem Apple described
    precisely, which is how an afternoon goes missing.
    """
    url = f"{BASE}/{version}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw}


def errs(d) -> str:
    """Apple's own words, joined. Use this in every failure message."""
    out = []
    for e in (d or {}).get("errors", []):
        bit = e.get("detail") or e.get("title") or ""
        src = (e.get("source") or {}).get("pointer") or ""
        out.append(f"{bit}{f'  [{src}]' if src else ''}")
    return "; ".join(out) or json.dumps(d)[:400]
