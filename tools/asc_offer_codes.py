"""Mint App Store offer codes for the Mac subscription, and upload them.

    python tools/asc_offer_codes.py list
    python tools/asc_offer_codes.py create-offer --name "Friends 2026" --months 3 --yes
    python tools/asc_offer_codes.py mint --offer <id> --count 10 --expires 2027-03-31 --yes
    python tools/asc_offer_codes.py fetch --batch <id> --out friends-2026.csv
    python tools/asc_offer_codes.py upload --csv friends-2026.csv --batch friends-2026 --yes

WHY THIS IS A LOCAL TOOL AND NOT A WORKER ROUTE
-----------------------------------------------
Minting needs an App Store Connect key, and an individual key carries App
Manager rights over the WHOLE app — edit the listing, change pricing, submit
builds. Apple has no finer grain. That credential must not sit in an
internet-facing Worker, so it stays here and only the resulting code STRINGS
are uploaded. `migrations/011-apple-offer-codes.sql` says the same thing from
the other side.

WHY OFFER CODES AT ALL
----------------------
Dawnlist's own override codes were removed from the Mac build under App Store
guideline 3.1.1. Nothing in the macOS app can redeem anything, so an App Store
offer code is the only way to give somebody free access to the Mac
subscription. This is not one option among several.

EVERY WRITE NEEDS `--yes`, AND SAYS WHAT IT WILL DO FIRST
---------------------------------------------------------
Minting is not reversible: Apple has no API to withdraw a one-time code once it
exists, and the only control over a code already handed out is the expiry set
when it was minted. So `--expires` is REQUIRED on `mint` rather than
defaulted — a code with no end date given to a friend is a permanent free
subscription, and a default would make that the thing that happens when nobody
is paying attention.

READ THIS BEFORE THE FIRST LIVE RUN. The request shapes below are written from
Apple's documented schema and have NOT been exercised against a live account.
One earlier attempt reached Apple and came back 409 naming only payload
problems — `totalNumberOfCodes` not allowed on create, `prices` required — and
said nothing about the subscription's state, which is why `create-offer`
supplies a price relationship and no code count. Run `list` first: it is
read-only and proves the credential and the subscription id before anything is
written.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request

from tools.asc import call, errs

#: The Mac subscription these codes unlock. From App Store Connect, confirmed
#: live: group 22370180, product com.spencerfields.dawnlist.monthly.
SUBSCRIPTION = "6809984690"

#: Apple's own ceiling is 25,000 codes per batch. The Worker takes 500 per
#: request, so a large batch is uploaded in chunks; see MAX_OFFER_CODES_PER_UPLOAD
#: in server/dawnlist-feed-worker/src/codes.js.
UPLOAD_CHUNK = 500

WORKER_BASE = "https://dawnlist-feed-worker.sgf36.workers.dev"


def _admin_licence() -> str:
    """The administrator licence the Worker authorises the upload with.

    Read from the credential store rather than taken as an argument: a licence
    key on a command line lands in the shell's history, and this one opens the
    admin console.
    """
    from app.core import credentials
    from app.core.entitlement import LICENCE_ACCOUNT, LICENCE_SERVICE

    key = credentials.read(LICENCE_SERVICE, LICENCE_ACCOUNT)
    if not key:
        raise SystemExit(
            "No licence in the credential store. Open Dawnlist, paste your "
            "administrator licence in Settings, and run this again.")
    return key


# ---------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------

def cmd_list(args) -> int:
    """Read-only. Proves the credential and the subscription before any write."""
    status, body = call(
        "GET", f"subscriptions/{SUBSCRIPTION}/offerCodes?limit=50")
    if status != 200:
        print(f"HTTP {status}: {errs(body)}")
        return 1
    rows = body.get("data", [])
    print(f"{len(rows)} offer code configuration(s) on {SUBSCRIPTION}:")
    for row in rows:
        a = row["attributes"]
        print(f"  {row['id']}  {a.get('name')!r}")
        print(f"     active={a.get('active')}  duration={a.get('duration')!r}  "
              f"mode={a.get('offerMode')!r}  periods={a.get('numberOfPeriods')}")
        st, codes = call(
            "GET", f"subscriptionOfferCodes/{row['id']}/oneTimeUseCodes?limit=50")
        for batch in codes.get("data", []) if st == 200 else []:
            ba = batch["attributes"]
            print(f"       batch {batch['id']}  codes={ba.get('numberOfCodes')}  "
                  f"expires={ba.get('expirationDate')}  active={ba.get('active')}")
    return 0


def cmd_create_offer(args) -> int:
    """Create the offer code configuration the codes will belong to."""
    payload = {
        "data": {
            "type": "subscriptionOfferCodes",
            "attributes": {
                "name": args.name,
                "customerEligibilities": ["NEW", "EXISTING", "EXPIRED"],
                "offerEligibility": "STACK_WITH_INTRO_OFFERS",
                "duration": args.duration,
                "offerMode": "FREE",
                "numberOfPeriods": args.periods,
            },
            "relationships": {
                "subscription": {
                    "data": {"type": "subscriptions", "id": SUBSCRIPTION}},
                # REQUIRED on create. A FREE offer still needs a price row per
                # territory; Apple refused an earlier attempt for its absence
                # and said nothing else was wrong.
                "prices": {"data": [
                    {"type": "subscriptionOfferCodePrices",
                     "id": f"${t}_0"} for t in args.territories]},
            },
        }
    }
    print("About to create, on subscription " + SUBSCRIPTION + ":")
    print(json.dumps(payload, indent=2))
    if not args.yes:
        print("\nNothing sent. Add --yes to create it.")
        return 0
    status, body = call("POST", "subscriptionOfferCodes", payload)
    print(f"HTTP {status}")
    if status not in (200, 201):
        print(errs(body) or json.dumps(body, indent=2))
        return 1
    print(f"offer id: {body['data']['id']}")
    return 0


def cmd_mint(args) -> int:
    """Create a batch of one-time-use codes on an existing offer."""
    payload = {
        "data": {
            "type": "subscriptionOfferCodeOneTimeUseCodes",
            "attributes": {
                "numberOfCodes": args.count,
                "expirationDate": args.expires,
            },
            "relationships": {
                "offerCode": {"data": {"type": "subscriptionOfferCodes",
                                       "id": args.offer}},
            },
        }
    }
    print(f"About to mint {args.count} code(s) on offer {args.offer}, "
          f"expiring {args.expires}.")
    print("THIS CANNOT BE UNDONE. Apple has no API to withdraw a minted code; "
          "the expiry above is the only control over one already handed out.")
    if not args.yes:
        print("\nNothing sent. Add --yes to mint them.")
        return 0
    status, body = call("POST", "subscriptionOfferCodeOneTimeUseCodes", payload)
    print(f"HTTP {status}")
    if status not in (200, 201):
        print(errs(body) or json.dumps(body, indent=2))
        return 1
    print(f"batch id: {body['data']['id']}")
    print("Now: fetch --batch <id> --out <file>.csv")
    return 0


def cmd_fetch(args) -> int:
    """Download the actual code strings. Apple returns CSV, not JSON."""
    from tools.asc import BASE, token

    url = f"{BASE}/v1/subscriptionOfferCodeOneTimeUseCodes/{args.batch}/values"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token()}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:400]}")
        return 1

    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"{args.out}: {len(text.splitlines())} line(s) written")
    print("TREAT THIS FILE AS A CREDENTIAL. Every line in it is free access.")
    return 0


# ---------------------------------------------------------------------------
# The Worker
# ---------------------------------------------------------------------------

def _codes_from_csv(path: str) -> list[str]:
    """Every code in Apple's CSV, however it labelled the column.

    Apple's export has varied between a bare list and a header row, so this
    takes any single-column value that looks like a code and drops the rest.
    The Worker validates again and refuses the whole upload on anything odd —
    this is convenience, not the check.
    """
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(io.StringIO(fh.read())))
    out = []
    for row in rows:
        for cell in row:
            value = (cell or "").strip().upper()
            if value and value.isalnum() and len(value) <= 40:
                out.append(value)
    return out


def cmd_upload(args) -> int:
    codes = _codes_from_csv(args.csv)
    if not codes:
        print(f"No codes found in {args.csv}")
        return 1
    print(f"{len(codes)} code(s) from {args.csv} -> batch {args.batch!r} "
          f"at {WORKER_BASE}")
    if not args.yes:
        print("\nNothing sent. Add --yes to upload.")
        return 0

    licence = _admin_licence()
    sent = 0
    for start in range(0, len(codes), UPLOAD_CHUNK):
        chunk = codes[start:start + UPLOAD_CHUNK]
        payload = json.dumps({"batch": args.batch, "codes": chunk,
                              "offer_id": args.offer}).encode()
        req = urllib.request.Request(
            f"{WORKER_BASE}/admin/apple-codes", data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {licence}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            print(f"  chunk at {start}: HTTP {e.code} {e.read().decode()[:300]}")
            print("  Re-running this command is safe: the Worker absorbs "
                  "codes it already has.")
            return 1
        sent += len(chunk)
        print(f"  {sent}/{len(codes)} sent; {body.get('in_batch')} now in the batch")
    print("Done. Assign them from Settings -> Admin.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="what offers and batches exist (read-only)")

    p = sub.add_parser("create-offer", help="create an offer code configuration")
    p.add_argument("--name", required=True)
    p.add_argument("--duration", default="THREE_MONTHS",
                   help="ONE_MONTH, TWO_MONTHS, THREE_MONTHS, SIX_MONTHS, ONE_YEAR")
    p.add_argument("--periods", type=int, default=1)
    p.add_argument("--territories", nargs="+", default=["GBR", "USA"])
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("mint", help="create one-time-use codes on an offer")
    p.add_argument("--offer", required=True)
    p.add_argument("--count", type=int, required=True)
    # Required, not defaulted: see the module docstring.
    p.add_argument("--expires", required=True, help="YYYY-MM-DD")
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("fetch", help="download the code strings as CSV")
    p.add_argument("--batch", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("upload", help="send a CSV to the admin console's ledger")
    p.add_argument("--csv", required=True)
    p.add_argument("--batch", required=True, help="the name YOU will see in the console")
    p.add_argument("--offer", default=None)
    p.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    return {
        "list": cmd_list,
        "create-offer": cmd_create_offer,
        "mint": cmd_mint,
        "fetch": cmd_fetch,
        "upload": cmd_upload,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
