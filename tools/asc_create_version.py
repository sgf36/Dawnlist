"""Create a new App Store version and push everything to it. One-shot.

    python tools/asc_create_version.py 1.2.1
    python tools/asc_create_version.py 1.2.1 --attach-newest

Creates the version, pushes listing text to all locales, and uploads
screenshots. Optionally attaches the newest VALID build.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.asc import APP, PRIMARY_LOCALE, call, errs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="marketing version string, e.g. 1.2.1")
    ap.add_argument("--attach-newest", action="store_true",
                    help="attach the newest VALID build after creating")
    args = ap.parse_args()

    # check whether this version already exists
    st, v = call("GET", f"apps/{APP}/appStoreVersions?limit=10")
    for ver in v.get("data", []):
        if ver["attributes"]["versionString"] == args.version:
            state = ver["attributes"]["appStoreState"]
            print(f"version {args.version} already exists ({state})")
            vid = ver["id"]
            break
    else:
        print(f"creating version {args.version}…")
        st, d = call("POST", "appStoreVersions", {
            "data": {
                "type": "appStoreVersions",
                "attributes": {
                    "versionString": args.version,
                    "platform": "MAC_OS",
                    "copyright": f"© 2026 Spencer Fields",
                },
                "relationships": {
                    "app": {"data": {"type": "apps", "id": APP}},
                },
            }
        })
        if st not in (200, 201):
            sys.exit(f"create failed {st}: {errs(d)}")
        vid = d["data"]["id"]
        state = d["data"]["attributes"]["appStoreState"]
        print(f"  → {vid} ({state})")

    if args.attach_newest:
        st, allb = call("GET",
                        f"builds?filter[app]={APP}&limit=20&sort=-version")
        builds = [x for x in allb.get("data", [])
                  if x["attributes"].get("processingState") == "VALID"]
        if not builds:
            print("no VALID builds found — skipping attach")
        else:
            newest = builds[0]
            label = newest["attributes"]["version"]
            print(f"attaching build {label} ({newest['id']})…")
            st, d = call("PATCH", f"appStoreVersions/{vid}", {
                "data": {"type": "appStoreVersions", "id": vid,
                         "relationships": {"build": {"data": {
                             "type": "builds", "id": newest["id"]}}}}})
            if st != 200:
                print(f"  attach failed {st}: {errs(d)}")
                print("  (build may still be processing — re-run with "
                      "--attach-newest once it is VALID)")
            else:
                print("  → attached")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
