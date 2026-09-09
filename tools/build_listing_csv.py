"""Build the Partner Center listing-import CSV, adding every language at once.

    .venv/Scripts/python tools/build_listing_csv.py <export.csv> [out.csv]

THE IMPORT CAN CREATE LANGUAGES, AND THAT IS THE WHOLE POINT OF THIS FILE.
Microsoft's documentation is explicit: "You can create listings for new
languages by adding the language-locale code into the next empty column in the
top row." A first attempt at this reasoned from the EXPORT — which only carries
columns for languages already selected — and wrongly concluded that the 46
languages had to be ticked by hand in an 830-row dialog first. They do not.

WHAT EACH NEW COLUMN MUST CARRY, per the same documentation:

  * Description — required for every listing.
  * Title — required "for languages which don't have associated packages",
    which is all 46 of them: the package declares en-US only. It names which
    RESERVED name to use, so it must be the reserved string exactly.
  * At least one screenshot. Asset URLs "can be reused in multiple
    descriptions", so every language points at the English assets and this
    uploads nothing. That keeps it a BARE CSV import rather than a folder
    import — and a folder import cannot be automated at all, because the
    dialog is a directory picker and a flat file list carries no relative
    paths.

THE LANGUAGE CODES ARE NOT ALL THE BARE TWO-LETTER FORM. Read off a real
Partner Center export that was accepted: `zh-hans`, `yo-latn`, `ig-latn` and
`en-us` differ. Guessing `zh` here fails the import with an error that names
no field and no language.

THREE OF THE APP'S LOCALES HAVE NO STORE LISTING LANGUAGE AT ALL — Burmese,
Javanese and Somali are absent from the Store's 104. The application still
speaks all fifty; those three simply cannot have localised store copy. They are
skipped explicitly rather than silently, so the count always adds up.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTING = ROOT / "store" / "listing"

#: app locale -> Partner Center language-locale code. Only the four that differ
#: are listed; everything else is the bare code.
CODE_OVERRIDES = {"en": "en-us", "zh": "zh-hans", "yo": "yo-latn", "ig": "ig-latn"}

#: Present in the app, absent from the Store's listing languages.
NO_STORE_LISTING = {"my": "Burmese", "jv": "Javanese", "so": "Somali"}

#: Must match a name reserved under Product management > Manage app names,
#: character for character. The em-dash form is NOT reserved and was rejected
#: at package upload on 2026-09-08.
TITLE = "Dawnlist Job Search"


#: Partner Center's own limits, per field. An over-length value is rejected at
#: IMPORT, after the file has been built and uploaded — and the message names
#: the field but not the language, so finding which of forty-seven is too long
#: means opening them one at a time.
#:
#: Checked here instead. Translations run longer than English far more often
#: than they run shorter: German and Finnish routinely add a third, and the
#: English copy that fits with room to spare is exactly the copy that produces
#: an overflow nobody predicted.
FIELD_LIMITS = {
    "Description": 10_000,
    "ShortDescription": 1_000,
    "ReleaseNotes": 1_500,
    "Feature": 200,
    "SearchTerm": 30,
    "DesktopScreenshotCaption": 200,
}


def limit_for(field: str) -> int | None:
    if field in FIELD_LIMITS:
        return FIELD_LIMITS[field]
    for prefix, cap in FIELD_LIMITS.items():
        if field.startswith(prefix):
            return cap
    return None


def check_limits(rows, header) -> list[str]:
    """Every over-length cell, named by field AND language."""
    problems = []
    for r in rows[1:]:
        cap = limit_for(r[0])
        if cap is None:
            continue
        for i, value in enumerate(r):
            if i < 3 or not value:
                continue
            if len(value) > cap:
                problems.append(
                    f"{r[0]} [{header[i]}] is {len(value)} chars, limit {cap}")
    return problems


def store_code(locale: str) -> str:
    return CODE_OVERRIDES.get(locale, locale)


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip().split("\n")[2].strip())
    export = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else export.with_name(
        export.stem + "-IMPORT.csv")

    rows = list(csv.reader(io.open(export, encoding="utf-8-sig")))
    header, body = rows[0], rows[1:]
    if "en-us" not in header:
        sys.exit("the export has no en-us column; nothing to copy screenshots from")
    en = header.index("en-us")

    # The English screenshot URLs, reused verbatim by every language.
    shots = {r[0]: r[en] for r in body
             if r[0].startswith("DesktopScreenshot") and len(r) > en and r[en].strip()}
    if not shots:
        sys.exit("no DesktopScreenshot URLs in en-us — import the English "
                 "screenshots first, then re-export. A listing needs one.")

    # ENGLISH IS INCLUDED, AND ONLY ITS EMPTY CELLS ARE FILLED.
    #
    # It used to be skipped outright, on the reasoning that the export already
    # carries en-us. That held while the only fields written were Description,
    # Title and the Features, all of which the export did carry. It stopped
    # holding the moment four fields were added that the export had EMPTY in
    # every language including English — so the primary listing, the one most
    # customers read, was the only one that got none of them.
    #
    # Filling only the blanks is the other half. Re-writing a non-empty en-us
    # cell would silently revert anything edited in Partner Center since the
    # export was taken, which is how a deploy from the wrong source quietly
    # undoes somebody's work.
    locales = []
    for p in sorted(LISTING.glob("*.json")):
        if p.stem in NO_STORE_LISTING:
            continue
        locales.append(p.stem)

    skipped = sorted(NO_STORE_LISTING)
    print(f"{len(locales)} language(s) to add; skipping {len(skipped)} with no "
          f"Store listing language: {', '.join(NO_STORE_LISTING[s] for s in skipped)}")
    print(f"reusing {len(shots)} English screenshot URL(s) per language")

    data = {loc: json.loads((LISTING / f"{loc}.json").read_text(encoding="utf-8"))
            for loc in locales}

    new_cols = [store_code(loc) for loc in locales]
    dupes = [c for c in new_cols if c in header]
    if dupes:
        print(f"note: already present, will be overwritten: {', '.join(dupes)}")

    header = header + [c for c in new_cols if c not in header]
    index = {c: i for i, c in enumerate(header)}

    out_rows = [header]
    for r in body:
        r = list(r) + [""] * (len(header) - len(r))
        field = r[0]
        for loc in locales:
            col = index[store_code(loc)]
            d = data[loc]
            # English: fill the gaps, never overwrite what is already there.
            if loc == "en" and r[col].strip():
                continue
            if field == "Description":
                r[col] = d["description"]
            elif field == "ShortDescription":
                # The line under the app name in search results. Empty, the
                # Store truncates the long description instead — which opens
                # "BEFORE YOU BUY — WHAT DAWNLIST DEPENDS ON", so the first
                # thing a browsing customer read was a warning.
                r[col] = d.get("short_description", "")
            elif field == "ReleaseNotes":
                # Read by people deciding whether to update. 1.1.0 exists
                # because the live build refuses every licence key, and the
                # export showed this field empty in every language.
                r[col] = d.get("release_notes", "")
            elif field.startswith("SearchTerm"):
                # Not shown to anyone; they are what the Store indexes. Seven
                # slots, 30 characters each, and the export had all seven
                # empty in every language — so nothing but the title and the
                # description was findable.
                try:
                    n = int(field[len("SearchTerm"):])
                except ValueError:
                    continue
                terms = d.get("search_terms") or []
                if 1 <= n <= len(terms):
                    r[col] = terms[n - 1]
            elif field.startswith("DesktopScreenshotCaption"):
                try:
                    n = int(field[len("DesktopScreenshotCaption"):])
                except ValueError:
                    continue
                caps = d.get("screenshot_captions") or []
                if 1 <= n <= len(caps):
                    r[col] = caps[n - 1]
            elif field == "Title":
                r[col] = TITLE
            elif field in shots:
                r[col] = shots[field]
            elif field.startswith("Feature"):
                try:
                    n = int(field[len("Feature"):])
                except ValueError:
                    continue
                feats = d.get("features") or []
                if 1 <= n <= len(feats):
                    r[col] = feats[n - 1]
        out_rows.append(r)

    problems = check_limits(out_rows, header)
    if problems:
        print(f"\n{len(problems)} field(s) over Partner Center's limit:",
              file=sys.stderr)
        for line in problems[:40]:
            print(f"  {line}", file=sys.stderr)
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more", file=sys.stderr)
        print("\nNot written. Shorten the source copy and re-run — an import "
              "rejects on the first one and does not say which language.",
              file=sys.stderr)
        return 1

    with io.open(out, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(out_rows)

    print(f"\nwrote {out}")
    print(f"  {len(header)} columns ({len(header) - 4} languages + Field/ID/Type/default)")
    print(f"  {len(out_rows) - 1} rows")
    print("\nImport with 'Import .csv' — NOT 'Import folder'. Nothing is")
    print("uploaded, because every asset is an existing Partner Center URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
