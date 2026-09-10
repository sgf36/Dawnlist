"""Upload the Mac App Store screenshots. Idempotent — safe to re-run.

    .venv/Scripts/python tools/asc_screenshots.py dist/mac-screenshots

THE IMAGES MUST BE RENDERED ON A MAC, which is what the `screenshots` job in
.github/workflows/build.yml exists for. Rendering here produces Windows fonts
and Windows control styling; the grabs take the widget rather than the window
so no title bar leaks, but a reviewer sees the buttons.

    gh workflow run build.yml --ref master
    gh run download <id> -n mac-screenshots -D dist/mac-screenshots

APPLE ACCEPTS ONLY 1280x800, 1440x900, 2560x1600 and 2880x1800 for macOS, and
every image in a set must match the set's declared size. A wrong size is
invisible until upload, so this checks before reserving anything.

WHY 2560x1600 AND NOT 1440x900. At 1440x900 the shortlist's "Why" column
truncates — "Named target employer; travel and lei..." — and those reasons are
the entire point of that screenshot. The trade is some empty table area at the
larger size, which is the lesser flaw: a cut-off reason undercuts the exact
claim the image exists to make.

THE UPLOAD IS FOUR STEPS AND THE MIDDLE TWO ARE EASY TO GET WRONG. Reserve with
POST /appScreenshots, PUT each chunk using ONLY the headers Apple's
uploadOperations name — an Authorization header there is rejected by the
storage endpoint — then PATCH uploaded=true with an MD5 checksum, then poll
until assetDeliveryState is COMPLETE. Apple accepts the PATCH and then fails
the asset asynchronously, so skipping the poll reports success on a broken
upload.
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

from tools.asc import APP, call, errs  # noqa: E402

#: The order they appear on the product page. `02-needs-review` is deliberately
#: absent: it is mostly empty white with a stale detail pane, and was dropped
#: from the Microsoft listing for the same reason.
#: FIVE, NOT SIX. `02-needs-review` is deliberately absent: it is mostly
#: empty white with a stale detail pane, and was dropped from the Microsoft
#: listing for the same reason.
#:
#: Restored 2026-09-09 after being removed by someone who did not read this
#: comment before deciding Apple's ten-slot allowance meant the omission was
#: a habit carried over from the other store. It is not. Look at the image:
#: one row above ninety percent empty striping, and a detail pane describing
#: a different posting from the one selected. A reviewer reads that as a
#: broken product, and they would be half right — see the note in
#: `store/MACOS-LISTING.md` about the pane not following a tab change.
ORDER = ["01-shortlist", "03-board", "04-understood", "05-calibration",
         "06-rules"]

EXPECTED = (2560, 1600)
DISPLAY_TYPE = "APP_DESKTOP"


def upload_one(set_id: str, path: Path) -> tuple[bool, str]:
    data = path.read_bytes()
    st, d = call("POST", "appScreenshots", {
        "data": {"type": "appScreenshots",
                 "attributes": {"fileName": path.name, "fileSize": len(data)},
                 "relationships": {"appScreenshotSet": {"data": {
                     "type": "appScreenshotSets", "id": set_id}}}}})
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

    st, d = call("PATCH", f"appScreenshots/{shot_id}", {
        "data": {"type": "appScreenshots", "id": shot_id,
                 "attributes": {"uploaded": True,
                                "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})
    if st != 200:
        return False, f"commit failed {st}: {errs(d)}"

    for _ in range(40):
        st, d = call("GET", f"appScreenshots/{shot_id}")
        state = (d.get("data", {}).get("attributes", {})
                 .get("assetDeliveryState") or {})
        if state.get("state") == "COMPLETE":
            return True, "COMPLETE"
        if state.get("errors"):
            return False, json.dumps(state["errors"])[:300]
        time.sleep(3)
    return False, "still not COMPLETE after two minutes"


def main() -> int:
    argv = [a for a in sys.argv[1:] if a != "--replace"]
    replace = "--replace" in sys.argv
    src = Path(argv[0] if argv else "dist/mac-screenshots")
    files = [src / f"{n}.png" for n in ORDER]
    missing = [p.name for p in files if not p.exists()]
    if missing:
        sys.exit(f"missing: {', '.join(missing)} — render on a Mac first")

    from PIL import Image
    wrong = [f"{p.name} is {Image.open(p).size}" for p in files
             if Image.open(p).size != EXPECTED]
    if wrong:
        sys.exit("Apple requires every image in a set to be the declared "
                 f"size {EXPECTED[0]}x{EXPECTED[1]}:\n  " + "\n  ".join(wrong))

    st, d = call("GET", f"apps/{APP}/appStoreVersions?limit=5")
    vid = d["data"][0]["id"]
    st, loc = call("GET", f"appStoreVersions/{vid}/appStoreVersionLocalizations")
    lid = loc["data"][0]["id"]
    print(f"version {d['data'][0]['attributes']['versionString']}, "
          f"locale {loc['data'][0]['attributes']['locale']}")

    st, sets = call("GET", f"appStoreVersionLocalizations/{lid}/appScreenshotSets")
    existing = [s for s in sets.get("data", [])
                if s["attributes"].get("screenshotDisplayType") == DISPLAY_TYPE]
    if existing:
        set_id = existing[0]["id"]
        st, shots = call("GET", f"appScreenshotSets/{set_id}/appScreenshots")
        have = len(shots.get("data", []))
        if replace:
            # WHY --replace EXISTS. "Already holds N" asks whether SOME
            # screenshots are there. It never asks whether they are the RIGHT
            # ones, so a set uploaded before a UI change stays stale for ever
            # and the tool reports success. On 2026-09-09 the listing held
            # five images of a board that no longer had those buttons, and
            # re-running this said "nothing to do".
            for shot in shots.get("data", []):
                st2, _ = call("DELETE", f"appScreenshots/{shot['id']}")
                if st2 not in (204, 200):
                    sys.exit(f"could not delete {shot['id']} -> {st2}")
            print(f"removed {have} existing screenshot(s) from set {set_id}")
        elif have >= len(files):
            print(f"set already holds {have} screenshot(s) — nothing to do.")
            print("Pass --replace to swap them for the ones in "
                  f"{src}: a count is not a comparison, and this will not "
                  "notice that the images are out of date.")
            return 0
        else:
            print(f"reusing set {set_id} ({have} already there)")
    else:
        st, d = call("POST", "appScreenshotSets", {
            "data": {"type": "appScreenshotSets",
                     "attributes": {"screenshotDisplayType": DISPLAY_TYPE},
                     "relationships": {"appStoreVersionLocalization": {
                         "data": {"type": "appStoreVersionLocalizations",
                                  "id": lid}}}}})
        if st != 201:
            sys.exit(f"creating the set -> {st}: {errs(d)}")
        set_id = d["data"]["id"]
        print(f"created set {set_id}")

    ok = 0
    for p in files:
        print(f"  {p.name:<22} ", end="", flush=True)
        good, why = upload_one(set_id, p)
        print(why)
        ok += good
    print(f"\n{ok}/{len(files)} uploaded")
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
