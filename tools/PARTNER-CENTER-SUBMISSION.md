# Partner Center Submission via msstore CLI

How to submit a new MSIX to the Microsoft Store for Dawnlist (9PF25H395BB8).

## Prerequisites

- `msstore` CLI installed and authenticated (`msstore apps list` shows Dawnlist)
- MSIX at a known path, built from `packaging/build_msix.py`
- The MSIX already zipped: `Compress-Archive -Path .\Dawnlist-store-iap.msix -DestinationPath .\package.zip`

## Why the obvious approach fails

The `msstore submission update` command does a **full PUT** to the Partner
Center Submission API. A PUT replaces the entire submission resource. Every
field absent from the body resets to its default. Three requirements that are
not documented anywhere obvious:

1. **Pricing is mandatory.** Even if the app is free, the JSON must carry
   `{"Pricing": {"PriceId": "Free", ...}}`. Without it the CLI emits a custom
   error before calling the API.

2. **Listings must contain at least one locale.** The API returns
   `InvalidParameterValue: The size of Listings/AppListings must be 1 or more`.
   With 47 locales this means the payload is ~700 KB.

3. **Screenshots are embedded in the listings.** Each locale's `BaseListing`
   carries an `Images` array referencing previously uploaded screenshots by ID.
   Omitting them deletes the screenshots from the submission.

Because of (2) and (3), the only safe approach is:

    GET the draft → modify it → PUT it back

## The working process

### 1. Ensure no pending submission exists

```powershell
msstore submission get 9PF25H395BB8
# If "Found Pending Submission" and its Status is CommitFailed or
# you want to start fresh:
msstore submission delete 9PF25H395BB8   # interactive, answers y
```

### 2. Create a draft and update it (Python)

```python
import json, subprocess, os, urllib.request, tempfile

PRODUCT_ID = "9PF25H395BB8"

def parse_json(text):
    idx = text.index("{")
    depth = 0
    for i, c in enumerate(text[idx:], idx):
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[idx:i+1])

def msstore_get():
    r = subprocess.run(["msstore", "submission", "get", PRODUCT_ID],
                       capture_output=True, text=True, encoding="utf-8")
    return parse_json(r.stdout + r.stderr)

# Create a draft (clones the published submission)
subprocess.run(["msstore", "submission", "update", PRODUCT_ID, "{}",
                "--skipInitialPolling"], capture_output=True)
# This creates the draft even though the update itself fails.

# Read the draft
sub = msstore_get()

# Modify what you need
sub["ApplicationPackages"] = [{
    "FileName": "Dawnlist-store-iap.msix",
    "FileStatus": "PendingUpload",
}]
for locale in sub.get("Listings", {}):
    sub["Listings"][locale]["BaseListing"]["ReleaseNotes"] = "..."

# Strip read-only fields
for key in ["Id", "Status", "StatusDetails", "FileUploadUrl", "FriendlyName"]:
    sub.pop(key, None)

# PUT the full payload
path = os.path.join(tempfile.gettempdir(), "payload.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(sub, f, ensure_ascii=False)

subprocess.run(["msstore", "submission", "update", PRODUCT_ID, path,
                "--skipInitialPolling"])
```

### 3. Upload the package ZIP

```python
sub = msstore_get()  # re-read to get FileUploadUrl
url = sub["FileUploadUrl"]

with open(ZIP_PATH, "rb") as f:
    data = f.read()
req = urllib.request.Request(url, data=data, method="PUT")
req.add_header("x-ms-blob-type", "BlockBlob")
req.add_header("Content-Type", "application/zip")
urllib.request.urlopen(req, timeout=300)
```

### 4. Commit

```powershell
msstore submission publish 9PF25H395BB8
```

### 5. Poll

```powershell
msstore submission status 9PF25H395BB8
```

## Known traps

- **`msstore submission delete` requires interactive confirmation.** It uses
  Spectre.Console which rejects piped input. Must be run in a terminal.

- **`--verbose` is essential for debugging.** Without it, `update` prints only
  "Error!" with no detail. With `--verbose`, the HTTP status and the full API
  error body are visible.

- **The draft inherits the published package at `Uploaded` status.** Setting
  `"FileStatus": "PendingUpload"` in the PUT body tells Partner Center to
  expect a new upload. The blob URL (`FileUploadUrl`) is set when the draft is
  created and stays valid until the draft is committed.

- **A committed submission that fails (`CommitFailed`) cannot be updated.**
  Delete it and start again.

- **Existing packages must be kept and marked `PendingDelete`.** The API
  rejects a PUT that drops existing package entries. To replace a package:
  keep every existing entry with `"FileStatus": "PendingDelete"` and append
  the new one with `"FileStatus": "PendingUpload"`. The error message names
  the missing package ID but does not explain the rule.

- **`updateMetadata` also requires Pricing** — same as `update`. This is a
  CLI-level check, not an API one.

- **Release notes are per-locale.** The PUT body must set `ReleaseNotes` on
  every locale's `BaseListing`; any locale left out loses its listing entirely.

- **The `msstore submission update` command creates-then-updates in one call.**
  When no pending submission exists, it clones the published one and applies
  the JSON as a PUT. The payload must be valid for the PUT, not just for
  creating the draft. If the PUT fails, a draft is left behind in
  `PendingCommit` — this is usable on the next run without deletion.
