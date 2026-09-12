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
without anything here noticing.

WHERE THE KEY IS, IS ALSO NOT IN THIS FILE. It used to be a literal path into
one developer's OneDrive, which made every script here unrunnable anywhere else
and published the shape of that person's filing on a public repository. Set
`ASC_KEY_PATH`, or store the path in Credential Manager under service
`dawnlist-asc`, account `key-path`.

TEAM KEYS AND INDIVIDUAL KEYS ARE SIGNED DIFFERENTLY
----------------------------------------------------
A **team** key carries an issuer id in `iss` and reaches EVERY app on the
account — measured 2026-09-12, the previous key listed Dawnlist, Easy-Post
Desktop, Easy-Post Mobile Companion and Wren. A key that opens four products is
not a Dawnlist secret to hold.

An **individual** key belongs to one user and inherits that user's app
restriction, so a user limited to Dawnlist yields a key limited to Dawnlist —
measured the same day, `apps?limit=50` returned exactly one. Its token has NO
`iss` at all and carries `sub: "user"` instead.

Sign an individual key the team way and Apple answers 401 with "provide a
properly configured and signed bearer token", which reads exactly like a wrong
or expired key and sends you looking in the wrong place. Both shapes were tried
against the same key to establish this rather than reasoned about.

So: configure an issuer id and you get a team token; leave it unset and you get
an individual one. There is deliberately no auto-detection — a silent guess
between two things that fail identically is worse than a setting.

THE KEY ID COMES FROM THE FILE, NOT FROM A CONSTANT. It used to be hard-coded,
which meant pointing `ASC_KEY_PATH` at a different key still sent the OLD key's
id in the token header: a 401 that blames the new key for the old one's name.
Apple names the download after the id — `AuthKey_<id>.p8` for a team key,
`ApiKey_<id>.p8` for an individual one — so the file already knows it.
`ASC_KEY_ID` overrides, for a file that has been renamed.

A JWT IS MINTED PER CALL WITH iat BACKDATED SIXTY SECONDS. Apple rejects a
token whose iat is in the future by its own clock, and a laptop that is a few
seconds fast produces a 401 that reads exactly like a bad key. Backdating costs
nothing and removes the failure mode.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request

#: Where to look for the .p8, in order. The environment variable wins so a
#: one-off run can point at a different key without touching the store.
KEY_ENV = "ASC_KEY_PATH"
KEY_ID_ENV = "ASC_KEY_ID"
ISSUER_ENV = "ASC_ISSUER_ID"
KEY_SERVICE = "dawnlist-asc"
KEY_ACCOUNT = "key-path"

#: Apple's own download naming. Both spellings are real.
KEY_FILENAME = re.compile(r"^(?:AuthKey|ApiKey)_([A-Za-z0-9]+)\.p8$")


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
            f"'{KEY_SERVICE}', '{KEY_ACCOUNT}', r'<path to the .p8>')\"")

    path = pathlib.Path(configured).expanduser()
    if not path.is_file():
        # Named, because a missing key and a wrong key both come back from
        # Apple as 401 and the message is identical.
        raise SystemExit(f"the configured App Store Connect key {path} does "
                         f"not exist")
    return path


def key_id(path: pathlib.Path | None = None) -> str:
    """The `kid` for the token header: the override, else the filename's."""
    configured = os.environ.get(KEY_ID_ENV)
    if configured:
        return configured
    path = path or key_path()
    match = KEY_FILENAME.match(path.name)
    if not match:
        raise SystemExit(
            f"cannot tell the key id from {path.name}. Apple names the "
            f"download AuthKey_<id>.p8 or ApiKey_<id>.p8; if this file was "
            f"renamed, set {KEY_ID_ENV} to the id shown in App Store Connect.")
    return match.group(1)


def issuer() -> str | None:
    """The team issuer id, or None for an individual key."""
    return os.environ.get(ISSUER_ENV) or None


def claims(iss: str | None, now: int) -> dict:
    """The token payload. Pure, so both shapes are testable without a key."""
    payload = {"iat": now - 60, "exp": now + 1140, "aud": "appstoreconnect-v1"}
    if iss:
        payload["iss"] = iss      # team key: every app on the account
    else:
        payload["sub"] = "user"   # individual key: this user's apps only
    return payload


def token() -> str:
    import jwt  # PyJWT is a tooling dependency, not one the app ships

    path = key_path()
    kid = key_id(path)
    return jwt.encode(
        claims(issuer(), int(time.time())),
        path.read_text(),
        algorithm="ES256",
        headers={"kid": kid, "typ": "JWT"})


#: Dawnlist in App Store Connect. Confirmed live 2026-09-08: the record already
#: existed with a MAC_OS 1.0 version in PREPARE_FOR_SUBMISSION.
APP = "6809836433"
BUNDLE = "com.spencerfields.dawnlist"

#: The App Store listing's primary locale, read from the record rather than
#: assumed. Localisation POSTs that use a locale the app does not declare are
#: rejected with a message that names neither the locale nor the field.
PRIMARY_LOCALE = "en-GB"

BASE = "https://api.appstoreconnect.apple.com"


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
