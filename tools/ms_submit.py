"""Submit a new MSIX to the Microsoft Store via the msstore CLI.

    python tools/ms_submit.py path/to/Dawnlist-store-iap.msix "Release notes here"
    python tools/ms_submit.py path/to/Dawnlist-store-iap.msix --release-notes-file store/listing/RELEASE-NOTES.md
    python tools/ms_submit.py path/to/Dawnlist-store-iap.msix --dry-run

Reads the full runbook at tools/PARTNER-CENTER-SUBMISSION.md for context.

Requires:
  - msstore CLI installed and authenticated (msstore apps list)
  - No pending submission (delete first: msstore submission delete 9PF25H395BB8)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

PRODUCT_ID = "9PF25H395BB8"


def parse_json(text: str) -> dict:
    idx = text.index("{")
    depth = 0
    for i, c in enumerate(text[idx:], idx):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[idx : i + 1])
    raise ValueError("no complete JSON object in msstore output")


def msstore_get() -> dict:
    r = subprocess.run(
        ["msstore", "submission", "get", PRODUCT_ID],
        capture_output=True, text=True, encoding="utf-8",
    )
    return parse_json(r.stdout + r.stderr)


def msstore_run(*args: str) -> tuple[str, int]:
    r = subprocess.run(
        ["msstore", *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    return r.stdout + r.stderr, r.returncode


def extract_release_notes(md_path: str) -> str:
    """Extract the text between the two --- fences in RELEASE-NOTES.md."""
    text = open(md_path, encoding="utf-8").read()
    parts = text.split("---")
    if len(parts) < 3:
        sys.exit(f"cannot find two --- fences in {md_path}")
    return parts[1].strip()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("msix", help="path to the .msix file")
    ap.add_argument("release_notes", nargs="?", default=None,
                    help="release notes text (inline)")
    ap.add_argument("--release-notes-file", "-f",
                    help="read release notes from a RELEASE-NOTES.md file")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the payload but do not push or commit")
    args = ap.parse_args()

    msix = os.path.abspath(args.msix)
    if not os.path.isfile(msix):
        sys.exit(f"MSIX not found: {msix}")

    if args.release_notes_file:
        rn = extract_release_notes(args.release_notes_file)
    elif args.release_notes:
        rn = args.release_notes
    else:
        rn_path = os.path.join(os.path.dirname(__file__), "..",
                               "store", "listing", "RELEASE-NOTES.md")
        if os.path.isfile(rn_path):
            rn = extract_release_notes(rn_path)
            print(f"Using release notes from {rn_path}")
        else:
            sys.exit("provide release notes as an argument, --release-notes-file, "
                     "or place store/listing/RELEASE-NOTES.md")

    print(f"Release notes ({len(rn)} chars):\n  {rn[:100]}...\n")

    # --- Step 1: Get the current submission ---
    print("Step 1: Reading current submission...")
    sub = msstore_get()
    status = sub.get("Status")
    if status in ("CommitFailed",):
        print(f"  Found broken submission (Status: {status})")
        print("  Delete it first:  msstore submission delete " + PRODUCT_ID)
        return 1
    if status == "Published":
        print(f"  No pending submission (last published: {sub['Id']})")
    else:
        print(f"  Found pending submission {sub['Id']} (Status: {status})")

    # --- Step 2: Create a draft ---
    # The msstore CLI creates a draft (cloning published) when `update` is
    # called with no pending submission. But it applies the JSON as a full PUT
    # immediately, so the payload must be valid enough to pass the API.
    # Strategy: build the full payload FIRST, then call update once.
    print("\nStep 2: Building payload from published submission...")
    # Mark existing packages as PendingDelete (API rejects dropping them)
    for pkg in sub.get("ApplicationPackages", []):
        pkg["FileStatus"] = "PendingDelete"
    # Add the new package
    sub["ApplicationPackages"].append({
        "FileName": os.path.basename(msix),
        "FileStatus": "PendingUpload",
    })
    for locale in sub.get("Listings", {}):
        sub["Listings"][locale]["BaseListing"]["ReleaseNotes"] = rn
    sub["PackageDeliveryOptions"] = {
        "PackageRollout": {
            "IsPackageRollout": False,
            "PackageRolloutPercentage": 0,
            "PackageRolloutStatus": "PackageRolloutNotStarted",
            "FallbackSubmissionId": "0",
        },
        "IsMandatoryUpdate": True,
        "MandatoryUpdateEffectiveDate": "2026-09-26T12:00:00Z",
    }
    for key in ["Id", "Status", "StatusDetails", "FileUploadUrl", "FriendlyName"]:
        sub.pop(key, None)

    payload_path = os.path.join(tempfile.gettempdir(), "ms_submit_payload.json")
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(sub, f, ensure_ascii=False, indent=2)
    print(f"  Payload: {os.path.getsize(payload_path):,} bytes, "
          f"{len(sub.get('Listings', {}))} locales")

    if args.dry_run:
        print(f"\nDry run — payload at {payload_path}")
        return 0

    # --- Step 3: Create draft + PUT the payload ---
    print("\nStep 3: Creating draft and updating submission...")
    out, rc = msstore_run("submission", "update", PRODUCT_ID, payload_path,
                          "--verbose", "--skipInitialPolling")
    if rc != 0:
        # Extract error
        if "MSStoreException" in out:
            idx = out.index("MSStoreException:")
            msg = out[idx:idx + 400].split("at MSStore")[0]
            print(f"  PUT failed: {msg}")
        else:
            print(f"  PUT failed (rc={rc}):\n{out[:500]}")
        return 1
    print("  PUT succeeded")

    # --- Step 4: Upload the package ---
    print("\nStep 4: Uploading package...")
    sub = msstore_get()
    upload_url = sub.get("FileUploadUrl")
    if not upload_url:
        print("  ERROR: no FileUploadUrl in draft")
        return 1

    zip_path = msix + ".zip"
    if not os.path.isfile(zip_path):
        print(f"  Creating ZIP...")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(msix, os.path.basename(msix))

    with open(zip_path, "rb") as f:
        data = f.read()
    print(f"  Uploading {len(data) / (1024*1024):.1f} MB...")

    req = urllib.request.Request(upload_url, data=data, method="PUT")
    req.add_header("x-ms-blob-type", "BlockBlob")
    req.add_header("Content-Type", "application/zip")
    req.add_header("Content-Length", str(len(data)))
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            print(f"  Upload: {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"  Upload failed: {e.code} {e.read().decode()[:300]}")
        return 1

    # --- Step 5: Commit ---
    print("\nStep 5: Committing for certification...")
    out, rc = msstore_run("submission", "publish", PRODUCT_ID)
    for line in out.strip().split("\n"):
        if line.strip():
            print(f"  {line.strip()}")
    if rc != 0:
        print("  Commit failed")
        return 1

    print("\nDone. Poll with:  msstore submission status " + PRODUCT_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
