"""Build the folder Partner Center needs to upload localised screenshots.

    .venv/Scripts/python tools/build_screenshot_folder.py <fresh-export.csv> <out-dir>

A CSV can only reference images Partner Center ALREADY holds, so new images
have to arrive through a folder import. That import cannot be automated: the
dialog is a `webkitdirectory` picker and a flat file list carries no
`webkitRelativePath`, so images resolve to nothing however the CSV names them.
Establishing that cost an earlier session an afternoon of elimination testing,
because the error blames the CSV.

FOUR RULES THE IMPORTER ENFORCES SILENTLY. All four give the same useless
error — either "We couldn't import listings for the following languages" with
the language list rendered BLANK, or "We couldn't process this .csv file.
Please export your listings again", which blames the export whatever the fault
actually was:

  1. A FOLDER, not a zip.
  2. EVERY IMAGE PATH MUST BEGIN WITH THE NAME OF THE FOLDER THAT GETS PICKED,
     and the images must sit directly in it. The dialog is a `webkitdirectory`
     picker and the first segment of every uploaded path is the SELECTED
     folder's own name — so a wrapper directory silently breaks this while the
     csv looks perfectly correct. This is the one that cost four imports.
  3. EXACTLY ONE .csv in the folder.
  4. NO HYPHEN IN AN IMAGE FILENAME. `de_1_shortlist.png`, never
     `de_01-shortlist.png`. The root folder may contain hyphens.

This script asserts all four by construction and then re-reads the csv it wrote
to prove them against the bytes on disk.

BLANK IS NOT EMPTY. The 41 languages without their own set get BLANK image
cells, which is documented as "leave unchanged" for image fields — they keep
the English URLs the .csv import already gave them. That is what makes this
safe to run after the text import rather than before, and it avoids putting an
asset URL beside a folder path in the same file.

SIZE IS THE THING THAT KILLS THESE. Easy-Post's 40-language import sat on
"Importing" for hours and failed with a blank language list at ~360 assets; the
same machinery finished in minutes at 63. Six locales times five screenshots is
30, and this refuses to build anything over 63.
"""
from __future__ import annotations

import csv
import io
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
SHOTS = ROOT / "store" / "screenshots"

#: The captured locales, and the Partner Center column each maps to. Matches
#: what Easy-Post ships: seven sets, the rest referencing English.
LOCALES = {"de": "de", "es": "es", "fr": "fr",
           "hi": "hi", "ja": "ja", "zh": "zh-hans"}

#: Read from the ONE definition, `tools/listing_slots.py`. This used to be a
#: local list that assumed slot order matched file order — it does not, and
#: the caption builder made the same assumption independently, so three of six
#: captions described the wrong picture in forty-seven languages. Two copies
#: of a fact is one copy too many when getting them out of step is invisible.
from listing_slots import slots as _slots

SLOTS = _slots()

MAX_ASSETS = 63


def asset_file(locale: str, slot: int, stem: str) -> str:
    """`de_5_needs_review.png` — the convention that has always imported.

    NO HYPHEN IN A FILENAME. This is the fourth silent rule, and it cost an
    import on 2026-09-09: the files went out as `de_01-needs-review.png` and
    Partner Center answered "We couldn't process this .csv file. Please export
    your listings again" — an error naming no field, no language and no
    reason, and pointing at the one thing that was not wrong.

    Easy-Post has imported forty languages this way for months
    (`store_assets/build_listing_import.py`, `asset_file`), and the note in
    memory is explicit: Store codes `ig-latn` and `yo-latn` would put a hyphen
    in a filename, "which the working convention never has... not worth being
    first to test on an importer whose error page names no field, no language
    and no reason". I was first to test it. It fails.

    The ROOT FOLDER may contain hyphens — Easy-Post's is
    `EasyPost-Store-Listings-IMPORT-v2-stage1` — so only the file part is
    rewritten here.

    The slot number goes in the name as well, so what belongs where is legible
    from a directory listing rather than only from the CSV.
    """
    slug = stem.split("-", 1)[-1].replace("-", "_")
    return f"{locale}_{slot}_{slug}.png"


def main() -> int:
    if len(sys.argv) < 3:
        sys.exit("usage: build_screenshot_folder.py <fresh-export.csv> "
                 "<package-folder>")
    export, folder = Path(sys.argv[1]), Path(sys.argv[2])

    # THE FOLDER YOU PASS IS THE FOLDER YOU PICK, AND ITS OWN NAME IS THE
    # PREFIX. This used to take an out-DIR and create `<out-dir>/dawnlist-shots`
    # inside it, which produced a wrapper — `Downloads/dawnlist-shots-v4/
    # dawnlist-shots/` — so the picker offered two folders and only one of them
    # worked.
    #
    # The importer reads a `webkitdirectory` upload, where the FIRST PATH
    # SEGMENT IS THE NAME OF THE FOLDER THE USER SELECTED. Pick the wrapper and
    # every image arrives as `dawnlist-shots-v4/dawnlist-shots/de_1.png` while
    # the CSV says `dawnlist-shots/de_1.png` — rule 2 broken, and the error
    # blames the CSV. Easy-Post has no wrapper: its delivered folder holds the
    # CSV and the images directly and is named exactly what the paths say,
    # which is why its imports have never hit this.
    #
    # Now the ambiguity cannot be built. There is one folder, its basename is
    # the prefix, and the check at the end re-reads the written CSV to prove
    # every path starts with it.
    root_name = folder.name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    rows = list(csv.reader(io.open(export, encoding="utf-8-sig")))
    header = rows[0]
    missing = [c for c in LOCALES.values() if c not in header]
    if missing:
        sys.exit(f"export has no column for: {', '.join(missing)}. Import the "
                 f"text .csv first so the languages exist.")

    copied = 0
    paths: dict[tuple[str, str], str] = {}
    for loc, col in LOCALES.items():
        for slot_no, (stem, slot) in enumerate(SLOTS, 1):
            src = SHOTS / loc / f"{stem}.png"
            if not src.exists():
                sys.exit(f"missing {src} — render it first")
            name = asset_file(loc, slot_no, stem)
            shutil.copy2(src, folder / name)
            # RULE 2: prefixed with the root folder name.
            paths[(col, slot)] = f"{root_name}/{name}"
            copied += 1

    if copied > MAX_ASSETS:
        sys.exit(f"{copied} assets exceeds the {MAX_ASSETS} that imports "
                 f"reliably. Split into chunks of seven languages.")

    captured = set(LOCALES.values())
    shot_slots = {slot for _stem, slot in SLOTS}

    out_rows = [header]
    for r in rows[1:]:
        r = list(r) + [""] * (len(header) - len(r))
        if r[0] in shot_slots:
            # BLANK EVERY LANGUAGE THAT IS NOT GETTING A NEW IMAGE, rather than
            # passing its existing URL through. Blank is documented as "leave
            # unchanged" for image fields, so those languages keep the English
            # screenshots — and it avoids putting an asset URL beside a folder
            # path in the same file, which is exactly what Easy-Post's staged
            # import was shaped to prevent.
            for i, col in enumerate(header):
                if i >= 4 and col not in captured:
                    r[i] = ""
            for (col, slot), rel in paths.items():
                if r[0] == slot:
                    r[header.index(col)] = rel
        out_rows.append(r)

    # RULE 3: exactly one .csv, and it lives beside the images.
    with io.open(folder / "listings.csv", "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(out_rows)

    csvs = list(folder.glob("*.csv"))
    assert len(csvs) == 1, f"expected one .csv, found {len(csvs)}"

    # READ BACK WHAT WAS WRITTEN, not what we meant to write. Every rule is
    # checked against the bytes on disk, and each check has a positive control
    # — a count that must be non-zero — so a check cannot pass by finding
    # nothing to look at.
    on_disk = list(csv.reader(io.open(csvs[0], encoding="utf-8-sig")))
    files = {p.name for p in folder.glob("*.png")}
    cited = 0
    for r in on_disk[1:]:
        if not r or r[0] not in shot_slots:
            continue
        for value in r[4:]:
            if not value.strip():
                continue
            cited += 1
            head, _, name = value.partition("/")
            if head != root_name:
                sys.exit(f"{value} is not prefixed with the folder's own name "
                         f"{root_name!r} — rule 2")
            if "/" in name:
                sys.exit(f"{value} is nested; the images must sit directly in "
                         f"the folder that gets picked")
            if "-" in name:
                sys.exit(f"{name} contains a hyphen — rule 4")
            if name not in files:
                sys.exit(f"{value} is cited by the csv and is not in the folder")
    if cited != copied:
        sys.exit(f"the csv cites {cited} images and {copied} were copied")

    print(f"{folder}")
    print(f"  {copied} images across {len(LOCALES)} locale(s), "
          f"{len(SLOTS)} slot(s) each")
    print(f"  1 csv, {len(out_rows) - 1} rows, {len(header)} columns")
    print(f"  {cited} paths, all prefixed {root_name}/ and all present")
    print()
    print("Import with 'Upload folder' and pick EXACTLY this folder:")
    print()
    print(f"    {folder.resolve()}")
    print()
    print("Not its parent, not a folder containing it, not a zip. The first")
    print("path segment the browser sends is the name of the folder you pick,")
    print(f"and the csv says every image is under {root_name}/.")
    print()
    print("The other 41 languages have blank image cells, which means leave")
    print("unchanged — they keep the English screenshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
