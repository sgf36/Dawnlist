"""Cancel the current review and attach the newest build. One-shot.

    python tools/asc_cancel_and_reattach.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.asc import APP, call, errs  # noqa: E402


def main() -> int:
    # find the active review submission
    st, rs = call("GET", f"apps/{APP}/reviewSubmissions"
                         f"?filter[state]=WAITING_FOR_REVIEW")
    subs = rs.get("data", [])
    if not subs:
        print("no WAITING_FOR_REVIEW submission found — version may already "
              "be editable")
    else:
        rs_id = subs[0]["id"]
        print(f"cancelling review submission {rs_id}…")
        st, d = call("PATCH", f"reviewSubmissions/{rs_id}", {
            "data": {"type": "reviewSubmissions", "id": rs_id,
                     "attributes": {"canceled": True}}})
        if st != 200:
            sys.exit(f"cancel failed {st}: {errs(d)}")
        print(f"  → {d['data']['attributes']['state']}")

    # find the newest valid build
    st, allb = call("GET", f"builds?filter[app]={APP}&limit=20&sort=-version")
    builds = [x for x in allb.get("data", [])
              if x["attributes"].get("processingState") == "VALID"]
    if not builds:
        sys.exit("no VALID builds found")
    newest = builds[0]
    build_num = newest["attributes"]["version"]
    print(f"\nattaching build {build_num} ({newest['id']})…")

    st, v = call("GET", f"apps/{APP}/appStoreVersions?limit=1")
    vid = v["data"][0]["id"]
    st, d = call("PATCH", f"appStoreVersions/{vid}", {
        "data": {"type": "appStoreVersions", "id": vid,
                 "relationships": {"build": {"data": {
                     "type": "builds", "id": newest["id"]}}}}})
    if st != 200:
        sys.exit(f"attach failed {st}: {errs(d)}")
    print(f"  → attached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
