"""Mint the owner's licence — the way Spencer runs his own software.

    python tools/mint_owner_licence.py

Prints a new licence key and the two SQL statements that install it. It writes
nothing and stores nothing: the key exists only in what it prints, so run it
when you are ready to paste, and treat the output as a credential.

WHY THIS EXISTS
---------------
Dawnlist gates the feed behind a licence, and the licence comes from a Paddle
purchase or from an override code. Neither route is right for the person who
wrote it: buying your own software to satisfy a gate you built is absurd, and
minting a code needs an admin licence you do not have yet — which is a
chicken-and-egg, because `/admin/codes` re-reads `licence_roles` on every
request and refuses anyone who is not already an administrator.

So the FIRST licence has to be seeded directly in D1. Every one after it can be
minted through the console with this one.

WHY NOT A BUILD FLAG, WHICH WOULD BE EASIER
-------------------------------------------
A "developer build" flag has to be kept out of every released package, and it
fails OPEN the day somebody forgets — a flag left in a shipped build unlocks
the product for everyone, silently, and the packaging guards exist because
exactly that happened on the sibling project. A hardcoded key in the client is
worse: it is a key that ships to every customer.

A row in `licences` is the honest version. It is auditable —

    SELECT licence_key, plan, created_at FROM licences WHERE plan = 'owner';

answers "who is not paying, and why" in one query — and it is revocable like
any other licence.

WHAT THE ROLE AND PLAN MEAN
---------------------------
  role  = 'admin'   The `licence_roles` table decides what the SERVER permits,
                    and it is re-read on every admin request. This is what lets
                    you mint trial and reviewer codes afterwards.
  plan  = 'owner'   Not sellable, and deliberately absent from the upgrade
                    ladder `/v1/plan` returns. It is capped at 2,000 postings a
                    day — not to restrain you, but to bound a mistake: a loop
                    that re-fetches without `excludeJobIds` spends real feed
                    credits against your own subscription, and a cap turns
                    "an accident cost the month" into "an accident cost a day".
"""
from __future__ import annotations

import secrets
import sys

# The Windows console is cp1252 and this script prints em dashes. Fourth file
# in this repository to need it; cheap to prevent, confusing to debug.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

# Mirrors newLicenceKey() in server/dawnlist-feed-worker/src/paddle.js. Kept
# identical on purpose: a key in a different shape still works, and then looks
# wrong forever in support, in logs and in the admin console.
KEY_PREFIX = "DAWN"
RANDOM_BYTES = 24
BODY_LENGTH = 32
GROUP = 8

#: Must match PLANS.owner in src/plans.js. Duplicated rather than imported
#: because that file is JavaScript; if they ever disagree, the D1 columns win —
#: they are what the request path enforces.
OWNER_CAPS = {
    "plan": "owner",
    "max_postings_per_day": 2000,
    "max_refreshes_per_day": 24,
    "max_saved_queries": 100,
}


def new_licence_key() -> str:
    # BASE 36, not hex. The Worker renders each byte with `toString(36)` padded
    # to two characters; hex would produce a key of the right shape from a
    # smaller alphabet, which is a weaker key that looks identical.
    body = "".join(_base36(b).rjust(2, "0")
                   for b in secrets.token_bytes(RANDOM_BYTES))[:BODY_LENGTH].upper()
    groups = [body[i:i + GROUP] for i in range(0, len(body), GROUP)]
    return f"{KEY_PREFIX}-" + "-".join(groups)


def _base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def main() -> int:
    key = new_licence_key()
    caps = OWNER_CAPS

    print("-- Dawnlist owner licence. Generated locally; nothing was saved.")
    print("-- Run against the LIVE database:")
    print("--   npx wrangler d1 execute dawnlist --remote --file owner.sql")
    print("--")
    print("-- Apply migrations 001 and 002 FIRST — the `plan` columns these")
    print("-- statements write do not exist before them.")
    print()
    print("INSERT INTO licences")
    print("  (licence_key, tier, status, plan,")
    print("   max_postings_per_day, max_refreshes_per_day, max_saved_queries)")
    print(f"VALUES ('{key}', 'managed', 'active', '{caps['plan']}',")
    print(f"        {caps['max_postings_per_day']}, "
          f"{caps['max_refreshes_per_day']}, {caps['max_saved_queries']});")
    print()
    # from_code is NULL and that is correct: this licence did not come from a
    # code. A fabricated code reference would make the audit trail lie.
    print("INSERT INTO licence_roles (licence_key, role, from_code)")
    print(f"VALUES ('{key}', 'admin', NULL);")
    print()
    print("-- Then, in the app: Settings -> licence key -> paste")
    print(f"--   {key}")
    print("--")
    print("-- The same key works on every machine you own and on every build —")
    print("-- direct download and Microsoft Store alike. Nothing binds a")
    print("-- licence to a device; the daily allowance is shared across them,")
    print("-- which is the behaviour you want and not a limitation.")
    print("--")
    print("-- Verify afterwards:")
    print("--   npx wrangler d1 execute dawnlist --remote --command \\")
    print("--     \"SELECT licence_key, plan, max_postings_per_day FROM licences "
          "WHERE plan = 'owner';\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
