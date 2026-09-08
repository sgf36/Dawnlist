"""Price and territory availability for the Dawnlist Mac App Store subscription.

    .venv/Scripts/python tools/asc_subscription_price.py

SEPARATE FROM asc_subscription.py ON PURPOSE. Availability is the field that
produces MISSING_METADATA, and `subscriptionAvailabilities` 404s rather than
reading empty when it has never been set — so a creation script that ended with
it would report success on the half that worked and leave the half that
matters unverified.

THE PRICE IS A PRICE POINT, NOT A NUMBER. Apple does not take "79.00"; it takes
the id of one of its own price points, and the set differs per territory and
per subscription. The $79 point for USA reports proceeds of $67.15, which is
Apple's 15% Small Business Programme rate and matches what
`store/STORE-COMPLIANCE.md` works out independently. If those two ever
disagree, believe Apple and fix the document.

PAGINATION IS NOT OPTIONAL. pricePoints returns 200 per page and there are 800;
$79 is on the fourth page. A single unpaged request finds nothing above about
$50 and looks exactly like "that price is not offered".
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.asc import call, errs  # noqa: E402

SUBSCRIPTION = "6809984690"
BASE_TERRITORY = "USA"
TARGET_PRICE = 79.00


def price_point() -> tuple[str, str]:
    url = (f"subscriptions/{SUBSCRIPTION}/pricePoints"
           f"?filter[territory]={BASE_TERRITORY}&limit=200")
    seen, pages = [], 0
    while url and pages < 20:
        st, d = call("GET", url)
        if st != 200:
            sys.exit(f"pricePoints -> {st}: {errs(d)}")
        seen += d.get("data", [])
        nxt = (d.get("links") or {}).get("next")
        url = nxt.split("/v1/", 1)[1] if nxt and "/v1/" in nxt else None
        pages += 1
    for p in seen:
        a = p["attributes"]
        if a.get("customerPrice") and abs(float(a["customerPrice"]) - TARGET_PRICE) < 0.01:
            return p["id"], a.get("proceeds")
    sys.exit(f"no ${TARGET_PRICE} price point in {len(seen)} points across {pages} pages")


def set_price(point_id: str) -> str:
    st, d = call("GET", f"subscriptions/{SUBSCRIPTION}/prices?limit=50")
    if st == 200 and d.get("data"):
        return f"already had {len(d['data'])} price row(s)"
    st, d = call("POST", "subscriptionPrices", {
        "data": {"type": "subscriptionPrices",
                 "attributes": {"preserveCurrentPrice": False},
                 "relationships": {
                     "subscription": {"data": {"type": "subscriptions",
                                               "id": SUBSCRIPTION}},
                     "subscriptionPricePoint": {
                         "data": {"type": "subscriptionPricePoints",
                                  "id": point_id}}}}})
    if st not in (200, 201):
        sys.exit(f"subscriptionPrices -> {st}: {errs(d)}")
    return "created"


def territories() -> list[str]:
    out, url, pages = [], "territories?limit=200", 0
    while url and pages < 10:
        st, d = call("GET", url)
        if st != 200:
            sys.exit(f"territories -> {st}: {errs(d)}")
        out += [t["id"] for t in d.get("data", [])]
        nxt = (d.get("links") or {}).get("next")
        url = nxt.split("/v1/", 1)[1] if nxt and "/v1/" in nxt else None
        pages += 1
    return out


def set_availability(terr: list[str]) -> str:
    st, _ = call("GET", f"subscriptions/{SUBSCRIPTION}/subscriptionAvailability")
    if st == 200:
        return "already set"
    st, d = call("POST", "subscriptionAvailabilities", {
        "data": {"type": "subscriptionAvailabilities",
                 "attributes": {"availableInNewTerritories": True},
                 "relationships": {
                     "subscription": {"data": {"type": "subscriptions",
                                               "id": SUBSCRIPTION}},
                     "availableTerritories": {
                         "data": [{"type": "territories", "id": t} for t in terr]}}}})
    if st not in (200, 201):
        sys.exit(f"subscriptionAvailabilities -> {st}: {errs(d)}")
    return f"created across {len(terr)} territories"


def main() -> int:
    # AVAILABILITY BEFORE PRICE, and the order is not cosmetic.
    #
    # Pricing first returns 409 "An error occurred while processing the pricing
    # information" pointing at the price point id — which reads as a bad price
    # point and is not one. A subscription cannot be priced in territories it
    # is not yet available in. Setting availability first makes the identical
    # price POST return 201. Measured 2026-09-09.
    terr = territories()
    print(f"availability  {set_availability(terr)}")
    pid, proceeds = price_point()
    print(f"price point   ${TARGET_PRICE:.2f} -> proceeds ${proceeds}")
    print(f"price         {set_price(pid)}")

    st, d = call("GET", f"subscriptions/{SUBSCRIPTION}")
    print(f"state         {d.get('data',{}).get('attributes',{}).get('state')}"
          if st == 200 else f"re-read -> {st}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
