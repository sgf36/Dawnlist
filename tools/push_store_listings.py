"""Push localized listing metadata to Partner Center via the msstore CLI.

Replaces the CSV export/import workflow. Reads the current draft submission,
patches Features and ReleaseNotes for every locale using the translations in
build_listings.T, and pushes the result with `msstore submission updateMetadata`.

    python tools/push_store_listings.py --dry-run   # diff, no push
    python tools/push_store_listings.py              # push to Partner Center
    python tools/push_store_listings.py --publish    # push and commit for cert

Requires the msstore CLI to be installed and authenticated:
    winget install "Microsoft Store Developer CLI"
    msstore apps list  # should show Dawnlist
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PRODUCT_ID = "9PF25H395BB8"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def get_submission() -> dict:
    result = subprocess.run(
        ["msstore", "submission", "get", PRODUCT_ID],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"msstore submission get failed (exit {result.returncode})")
    text = result.stdout
    return json.loads(text[text.index("{"):])


def build_payload(sub: dict, translations: dict, en_features: list[str]) -> dict:
    payload = {"Listings": {}}
    for locale, listing_data in sub["Listings"].items():
        base = listing_data.get("BaseListing", {})
        old_features = base.get("Features", [])
        t = translations.get(locale)
        if not t:
            continue

        if locale == "en-us":
            new_features = en_features
        else:
            new_features = list(old_features)
            if len(new_features) == 17:
                new_features.insert(4, t["f1"])
                new_features.insert(5, t["f2"])
                new_features.insert(8, t["f3"])
            elif len(new_features) == 20:
                new_features[4] = t["f1"]
                new_features[5] = t["f2"]
                new_features[8] = t["f3"]
            else:
                new_features.insert(4, t["f1"])
                new_features.insert(5, t["f2"])
                new_features.insert(8, t["f3"])

        payload["Listings"][locale] = {
            "BaseListing": {
                "ReleaseNotes": t["rn"],
                "Features": new_features,
            }
        }
    return payload


def push_metadata(payload: dict) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8",
    ) as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp = f.name

    result = subprocess.run(
        ["msstore", "submission", "updateMetadata", PRODUCT_ID, tmp],
        capture_output=True, text=True, encoding="utf-8",
    )
    Path(tmp).unlink(missing_ok=True)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(
            f"msstore submission updateMetadata failed (exit {result.returncode})")
    print(result.stdout)


def publish() -> None:
    result = subprocess.run(
        ["msstore", "submission", "publish", PRODUCT_ID],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(
            f"msstore submission publish failed (exit {result.returncode})")
    print(result.stdout)
    print("Submitted for certification. Poll with:")
    print(f"  msstore submission poll {PRODUCT_ID}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change without pushing")
    ap.add_argument("--publish", action="store_true",
                    help="after pushing metadata, commit the submission for certification")
    args = ap.parse_args()

    from dist.build_listings import T, EN_FEATURES

    print(f"Fetching current submission for {PRODUCT_ID}...")
    sub = get_submission()
    payload = build_payload(sub, T, EN_FEATURES)

    print(f"Prepared {len(payload['Listings'])} locale updates")
    for loc, data in sorted(payload["Listings"].items()):
        bl = data["BaseListing"]
        print(f"  {loc}: {len(bl['Features'])} features, "
              f"{len(bl['ReleaseNotes'])} chars release notes")

    if args.dry_run:
        out = Path("dist/listing-update-all.json")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"\nDry run — written to {out}. No changes pushed.")
        return 0

    print("\nPushing metadata...")
    push_metadata(payload)

    if args.publish:
        print("\nCommitting for certification...")
        publish()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
