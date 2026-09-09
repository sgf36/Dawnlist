"""Fill the subscription's price schedule for every available territory.

    .venv/Scripts/python tools/asc_subscription_price_fill.py

WHY THIS EXISTS, AND WHY asc_subscription_price.py IS NOT ENOUGH. That script
sets ONE price — the $79 USA base — and then short-circuits on
"already had N price row(s)". In the App Store Connect interface, choosing a
base price silently generates the equalised schedule for every other
territory. THE API DOES NOT DO THAT. It left the subscription available in 175
territories with a price in exactly one, and a subscription that is sellable
somewhere it has no price is incomplete — which is what held it at
MISSING_METADATA long after the localisation, availability, review note and
review screenshot were all in place. Every one of those read as present, so the
state looked inexplicable until the price rows were counted.

HOW THE OTHER 174 PRICES ARE CHOSEN. Not by arithmetic — Apple will not take a
number. `subscriptionPricePoints/{id}/equalizations` returns Apple's own
equivalent price point per territory for a given base point, which is exactly
what the interface uses. Each comes back at $79.00 / $67.15 proceeds, the 15%
Small Business Programme rate.

IDEMPOTENT. Territories that already have a price row are skipped, so a partial
run can simply be repeated.
"""
from __future__ import annotations

import base64
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.asc import call, errs  # noqa: E402

SUBSCRIPTION = "6809984690"


def paged(url: str) -> list[dict]:
    """Every page. The equalisations list is 174 long and pages at 200, but a
    territory list that silently truncated would leave gaps that look exactly
    like the bug this script fixes."""
    out, pages = [], 0
    while url and pages < 30:
        st, d = call("GET", url)
        if st != 200:
            sys.exit(f"{url} -> {st}: {errs(d)}")
        out += d.get("data", [])
        nxt = (d.get("links") or {}).get("next")
        url = nxt.split("/v1/", 1)[1] if nxt and "/v1/" in nxt else None
        pages += 1
    return out


def territory_of(price_point_id: str) -> str | None:
    """Apple encodes the territory in the price point id, which saves an
    include= round trip per point. Falls back to None if that ever changes."""
    try:
        pad = price_point_id + "=" * (-len(price_point_id) % 4)
        return json.loads(base64.b64decode(pad)).get("t")
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    # What is already priced.
    # BOTH relationships, not just territory: the price point id is what the
    # equalisations are looked up from, and asking for only the territory
    # leaves it absent so the base row reads as missing.
    rows = paged(f"subscriptions/{SUBSCRIPTION}/prices"
                 f"?include=territory,subscriptionPricePoint&limit=200")
    priced = set()
    base_point = None
    for r in rows:
        rel = r.get("relationships", {})
        t = (rel.get("territory", {}).get("data") or {}).get("id")
        if t:
            priced.add(t)
        if t == "USA":
            base_point = (rel.get("subscriptionPricePoint", {}).get("data") or {}).get("id")
    print(f"already priced in {len(priced)} territory(ies): "
          f"{', '.join(sorted(priced)) if len(priced) < 8 else '...'}")
    if not base_point:
        sys.exit("no USA base price row — run asc_subscription_price.py first")

    # How many territories the subscription is actually sold in.
    st, av = call("GET", f"subscriptions/{SUBSCRIPTION}/subscriptionAvailability"
                         f"?include=availableTerritories&limit[availableTerritories]=1")
    total = ((av.get("data", {}).get("relationships", {})
              .get("availableTerritories", {}).get("meta") or {})
             .get("paging", {}).get("total"))
    print(f"available in {total} territory(ies)")

    equalized = paged(f"subscriptionPricePoints/{base_point}/equalizations?limit=200")
    print(f"Apple offers {len(equalized)} equalised price point(s)")

    made, skipped, failed = 0, 0, []
    for p in equalized:
        terr = territory_of(p["id"])
        if terr and terr in priced:
            skipped += 1
            continue
        rel = {"subscription": {"data": {"type": "subscriptions",
                                         "id": SUBSCRIPTION}},
               "subscriptionPricePoint": {"data": {
                   "type": "subscriptionPricePoints", "id": p["id"]}}}
        if terr:
            rel["territory"] = {"data": {"type": "territories", "id": terr}}
        st, d = call("POST", "subscriptionPrices", {
            "data": {"type": "subscriptionPrices",
                     "attributes": {"preserveCurrentPrice": False},
                     "relationships": rel}})
        if st in (200, 201):
            made += 1
            if made % 25 == 0:
                print(f"  ...{made} created")
        else:
            failed.append(f"{terr or p['id'][:12]}: {st} {errs(d)[:120]}")

    print(f"\ncreated {made}, skipped {skipped}, failed {len(failed)}")
    for f in failed[:10]:
        print("  FAILED", f)

    rows = paged(f"subscriptions/{SUBSCRIPTION}/prices?limit=200")
    print(f"price rows now: {len(rows)}")
    st, s = call("GET", f"subscriptions/{SUBSCRIPTION}?fields[subscriptions]=state")
    print("subscription state:",
          s.get("data", {}).get("attributes", {}).get("state"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
