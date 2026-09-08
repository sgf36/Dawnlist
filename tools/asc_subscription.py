"""Create Dawnlist's Mac App Store subscription. Idempotent — safe to re-run.

    .venv/Scripts/python tools/asc_subscription.py

WHY A SUBSCRIPTION AND NOT A LICENCE KEY
-----------------------------------------
Apple's guideline 3.1.1 forbids unlocking functionality with a licence key, so
the Mac App Store build cannot take the Paddle key that both Windows channels
use. Entitlement on macOS comes from the App Store receipt, which
`app/core/mac_receipt.py` trades with the Worker for a session token.

That whole path is already built and tested, and it has nothing to validate
against until this subscription exists. A MAS build shipped without it reaches
no feed at all.

PRICE
-----
$79/month, the same headline price as Paddle. Apple is NOT the same money:
Apple takes 15% under the Small Business Programme, so $79 nets $67.15 against
Paddle's $74.55. That is worked out in `store/STORE-COMPLIANCE.md` and moves
break-even from 18 subscribers to 20. Do not re-derive it here; if the price
changes, change it there first.

WHAT THIS SCRIPT DOES NOT DO
-----------------------------
Price and territory availability are deliberately a SEPARATE step
(`asc_subscription_price.py`). Availability is the field that produces
MISSING_METADATA later, and the resource 404s rather than reading empty when
it has never been set — so it needs its own verification rather than being
buried at the end of a creation script that has already reported success.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.asc import APP, PRIMARY_LOCALE, call, errs  # noqa: E402

GROUP_REFERENCE = "Dawnlist"
PRODUCT_ID = "com.spencerfields.dawnlist.monthly"
SUBSCRIPTION_REFERENCE = "Dawnlist Monthly"

#: Shown to customers. Apple caps the display name at 30 characters and the
#: description at 45; both are rejected without saying which field was too long.
DISPLAY_NAME = "Dawnlist"
DESCRIPTION = "The job feed, read and judged daily"

REVIEW_NOTE = (
    "Unlocks the daily job feed. Dawnlist is free to install and the "
    "application is fully usable without this subscription except for the "
    "morning run, which is what the subscription pays for. The application "
    "also asks for the customer's own Anthropic API key; that is stated "
    "before purchase and is not a second charge from us."
)


def step(label, fn):
    print(f"  {label} ... ", end="", flush=True)
    out = fn()
    print(out[1])
    return out[0]


def group_id():
    st, d = call("GET", f"apps/{APP}/subscriptionGroups?limit=50")
    if st != 200:
        sys.exit(f"listing groups -> {st}: {errs(d)}")
    for g in d.get("data", []):
        if g["attributes"].get("referenceName") == GROUP_REFERENCE:
            return g["id"], "already existed"
    st, d = call("POST", "subscriptionGroups", {
        "data": {"type": "subscriptionGroups",
                 "attributes": {"referenceName": GROUP_REFERENCE},
                 "relationships": {"app": {"data": {"type": "apps", "id": APP}}}}})
    if st not in (200, 201):
        sys.exit(f"creating group -> {st}: {errs(d)}")
    return d["data"]["id"], "created"


def group_localisation(gid):
    st, d = call("GET", f"subscriptionGroups/{gid}/subscriptionGroupLocalizations?limit=50")
    if st == 200:
        for loc in d.get("data", []):
            if loc["attributes"].get("locale") == PRIMARY_LOCALE:
                return loc["id"], "already existed"
    st, d = call("POST", "subscriptionGroupLocalizations", {
        "data": {"type": "subscriptionGroupLocalizations",
                 "attributes": {"name": DISPLAY_NAME, "locale": PRIMARY_LOCALE},
                 "relationships": {"subscriptionGroup": {
                     "data": {"type": "subscriptionGroups", "id": gid}}}}})
    if st not in (200, 201):
        sys.exit(f"group localisation -> {st}: {errs(d)}")
    return d["data"]["id"], "created"


def subscription(gid):
    st, d = call("GET", f"subscriptionGroups/{gid}/subscriptions?limit=50")
    if st == 200:
        for s in d.get("data", []):
            if s["attributes"].get("productId") == PRODUCT_ID:
                return s["id"], "already existed"
    st, d = call("POST", "subscriptions", {
        "data": {"type": "subscriptions",
                 "attributes": {
                     "name": SUBSCRIPTION_REFERENCE,
                     "productId": PRODUCT_ID,
                     "subscriptionPeriod": "ONE_MONTH",
                     "familySharable": False,
                     "reviewNote": REVIEW_NOTE,
                 },
                 "relationships": {"group": {
                     "data": {"type": "subscriptionGroups", "id": gid}}}}})
    if st not in (200, 201):
        sys.exit(f"creating subscription -> {st}: {errs(d)}")
    return d["data"]["id"], "created"


def subscription_localisation(sid):
    st, d = call("GET", f"subscriptions/{sid}/subscriptionLocalizations?limit=50")
    if st == 200:
        for loc in d.get("data", []):
            if loc["attributes"].get("locale") == PRIMARY_LOCALE:
                return loc["id"], "already existed"
    st, d = call("POST", "subscriptionLocalizations", {
        "data": {"type": "subscriptionLocalizations",
                 "attributes": {"name": DISPLAY_NAME,
                                "description": DESCRIPTION,
                                "locale": PRIMARY_LOCALE},
                 "relationships": {"subscription": {
                     "data": {"type": "subscriptions", "id": sid}}}}})
    if st not in (200, 201):
        sys.exit(f"subscription localisation -> {st}: {errs(d)}")
    return d["data"]["id"], "created"


def main() -> int:
    print(f"Dawnlist subscription ({PRODUCT_ID})")
    gid = step("group               ", group_id)
    step("group localisation  ", lambda: group_localisation(gid))
    sid = step("subscription        ", lambda: subscription(gid))
    step("localisation        ", lambda: subscription_localisation(sid))

    st, d = call("GET", f"subscriptions/{sid}")
    a = d.get("data", {}).get("attributes", {}) if st == 200 else {}
    print()
    print(f"  group id        {gid}")
    print(f"  subscription id {sid}")
    print(f"  state           {a.get('state')}")
    print()
    print("NEXT: price and territories are NOT set. Run asc_subscription_price.py.")
    print("A subscription with no price never leaves MISSING_METADATA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
