"""Upload the subscription's App Review screenshot. Idempotent — safe to re-run.

    .venv/Scripts/python tools/asc_subscription_screenshot.py dist/review/subscription-review.png

WHAT THIS UNBLOCKS. `com.spencerfields.dawnlist.monthly` sits at
MISSING_METADATA with exactly one requirement outstanding: a screenshot of the
screen a customer buys on. This uploads it and the state clears.

IT IS A DIFFERENT RESOURCE FROM THE APP SCREENSHOTS. Those hang off an
appStoreVersionLocalization and come in sized sets;
`subscriptionAppStoreReviewScreenshots` hangs off the subscription itself,
there is exactly ONE of it, and Apple imposes no fixed dimensions — so
tools/asc_screenshots.py cannot be pointed at this and the size check it
performs does not apply here.

THE UPLOAD IS FOUR STEPS AND THE MIDDLE TWO ARE EASY TO GET WRONG. Reserve,
then PUT each chunk using ONLY the headers Apple's uploadOperations name — an
Authorization header there is rejected by the storage endpoint — then PATCH
uploaded=true with an MD5, then poll until assetDeliveryState is COMPLETE.
Apple accepts the PATCH and then fails the asset asynchronously, so skipping
the poll reports success on a broken upload.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.asc import call, errs  # noqa: E402

#: The subscription created 2026-09-09, group 22370180.
SUBSCRIPTION = "6809984690"


def existing(sub: str):
    """The screenshot already on the subscription, if any."""
    st, d = call("GET", f"subscriptions/{sub}/appStoreReviewScreenshot")
    if st == 200 and d.get("data"):
        return d["data"]
    return None


def upload(sub: str, path: Path) -> tuple[bool, str]:
    data = path.read_bytes()
    st, d = call("POST", "subscriptionAppStoreReviewScreenshots", {
        "data": {"type": "subscriptionAppStoreReviewScreenshots",
                 "attributes": {"fileName": path.name, "fileSize": len(data)},
                 "relationships": {"subscription": {"data": {
                     "type": "subscriptions", "id": sub}}}}})
    if st != 201:
        return False, f"reserve failed {st}: {errs(d)}"
    shot = d["data"]
    shot_id = shot["id"]

    for op in shot["attributes"]["uploadOperations"]:
        chunk = data[op["offset"]:op["offset"] + op["length"]]
        req = urllib.request.Request(op["url"], method=op["method"], data=chunk)
        for h in op["requestHeaders"]:
            req.add_header(h["name"], h["value"])
        try:
            urllib.request.urlopen(req, timeout=300).read()
        except urllib.error.HTTPError as exc:
            return False, f"chunk PUT failed {exc.code}"

    st, d = call("PATCH", f"subscriptionAppStoreReviewScreenshots/{shot_id}", {
        "data": {"type": "subscriptionAppStoreReviewScreenshots", "id": shot_id,
                 "attributes": {"uploaded": True,
                                "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})
    if st != 200:
        return False, f"commit failed {st}: {errs(d)}"

    for _ in range(40):
        st, d = call("GET", f"subscriptionAppStoreReviewScreenshots/{shot_id}")
        state = (d.get("data", {}).get("attributes", {})
                 .get("assetDeliveryState") or {})
        if state.get("state") == "COMPLETE":
            return True, "COMPLETE"
        if state.get("errors"):
            return False, json.dumps(state["errors"])[:300]
        time.sleep(3)
    return False, "still not COMPLETE after two minutes"


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1
               else "dist/review/subscription-review.png")
    if not src.exists():
        sys.exit(f"missing: {src} — render it first (tools/render_subscribe.py)")

    have = existing(SUBSCRIPTION)
    if have:
        state = (have["attributes"].get("assetDeliveryState") or {}).get("state")
        print(f"a review screenshot already exists ({have['id']}, {state})")
        if "--replace" not in sys.argv:
            print("nothing to do; pass --replace to overwrite")
            return 0
        st, _ = call("DELETE",
                     f"subscriptionAppStoreReviewScreenshots/{have['id']}")
        print(f"deleted the old one -> {st}")

    print(f"uploading {src.name} ({src.stat().st_size} bytes) ", end="", flush=True)
    ok, why = upload(SUBSCRIPTION, src)
    print(why)
    if not ok:
        return 1

    st, d = call("GET", f"subscriptions/{SUBSCRIPTION}?fields[subscriptions]=state")
    print("subscription state is now:",
          d.get("data", {}).get("attributes", {}).get("state"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
