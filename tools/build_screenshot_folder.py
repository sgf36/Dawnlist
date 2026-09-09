"""Build the folder Partner Center needs to upload localised screenshots.

    .venv/Scripts/python tools/build_screenshot_folder.py <fresh-export.csv> <out-dir>

A CSV can only reference images Partner Center ALREADY holds, so new images
have to arrive through a folder import. That import cannot be automated: the
dialog is a `webkitdirectory` picker and a flat file list carries no
`webkitRelativePath`, so images resolve to nothing however the CSV names them.
Establishing that cost an earlier session an afternoon of elimination testing,
because the error blames the CSV.

THREE RULES THE IMPORTER ENFORCES SILENTLY. All three give the identical,
useless error — "We couldn't import listings for the following languages" with
the language list rendered BLANK — and it reads like a portal fault:

  1. A FOLDER, not a zip.
  2. IMAGE PATHS MUST INCLUDE THE ROOT FOLDER NAME. `dawnlist-shots/de_01.png`,
     never `de_01.png`. This is the one that cost two failed imports.
  3. EXACTLY ONE .csv in the folder.

This script asserts all three by construction.

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
SHOTS = ROOT / "store" / "screenshots"

#: The captured locales, and the Partner Center column each maps to. Matches
#: what Easy-Post ships: seven sets, the rest referencing English.
LOCALES = {"de": "de", "es": "es", "fr": "fr",
           "hi": "hi", "ja": "ja", "zh": "zh-hans"}

#: Screenshot file -> Partner Center slot, in the order the English ones were
#: uploaded. Keep them aligned or a caption describes the wrong picture.
SLOTS = [("01-shortlist", "DesktopScreenshot1"),
         ("03-board", "DesktopScreenshot2"),
         ("04-understood", "DesktopScreenshot3"),
         ("05-calibration", "DesktopScreenshot4"),
         ("06-rules", "DesktopScreenshot5")]

MAX_ASSETS = 63


def main() -> int:
    if len(sys.argv) < 3:
        sys.exit("usage: build_screenshot_folder.py <fresh-export.csv> <out-dir>")
    export, outdir = Path(sys.argv[1]), Path(sys.argv[2])

    root_name = "dawnlist-shots"
    folder = outdir / root_name
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
        for stem, slot in SLOTS:
            src = SHOTS / loc / f"{stem}.png"
            if not src.exists():
                sys.exit(f"missing {src} — render it first")
            name = f"{loc}_{stem}.png"
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

    print(f"{folder}")
    print(f"  {copied} images across {len(LOCALES)} locale(s), "
          f"{len(SLOTS)} slot(s) each")
    print(f"  1 csv, {len(out_rows) - 1} rows, {len(header)} columns")
    print()
    print("Import with 'Upload folder' and pick the FOLDER ITSELF, not a zip")
    print("and not its contents. The other 41 languages have blank image cells,")
    print("which means leave unchanged — they keep the English screenshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
