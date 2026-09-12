"""Turn the add-on's Partner Center export into a 47-language import.

    python tools/build_addon_listing_import.py <fresh-export.csv> [-o out.csv]

BUILD FROM A FRESH EXPORT, ALWAYS. Partner Center rejects an import whose base
is stale with "We couldn't process this .csv file. Please export your listings
again." — it is not a content error and reads like one. Export the CURRENT
submission immediately before running this.

WHY THIS IS NOT THE APP'S IMPORT PROBLEM
----------------------------------------
`feedback-partner-center-listing-import` records that the APP listing must go in
as a staged pair, never one full folder: 47 languages each referencing folder
images makes Partner Center mint hundreds of assets and fail with a blank
language list. None of that applies here. An add-on listing has **no
screenshots** — the export is three fields, one of which is an optional logo —
so there are no per-language assets to mint and a single CSV is correct.

THE LANGUAGE CODES ARE NOT OURS. Partner Center names four of them differently
from `app/i18n.py`, and getting one wrong drops that language silently rather
than erroring:

    ours   en        zh          yo         ig
    theirs en-us     zh-hans     yo-latn    ig-latn

The full set below was read off a REAL export of the sibling product
(`9NDSDL5LV5B5`, 47 languages) rather than guessed, because a code Partner
Center does not recognise becomes a column it ignores.

THE APP SUPPORTS MORE LANGUAGES THAN THE STORE TAKES. Any locale we translate
that has no Store column is reported and skipped — it is not an error, it is
the Store's list being shorter than ours, and saying so beats wondering later
why a file went unused.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ADDON = pathlib.Path(__file__).resolve().parents[1] / "store" / "listing-addon"

#: Partner Center's language columns, in its own order, read off a real export.
STORE_LANGUAGES = [
    "en-us", "zh-hans", "hi", "es", "fr", "ar", "bn", "pt", "ru", "ur", "id",
    "de", "ja", "mr", "te", "tr", "ta", "vi", "ko", "fa", "ha", "sw", "it",
    "pa", "gu", "am", "th", "kn", "yo-latn", "uz", "ml", "or", "uk", "pl",
    "ms", "nl", "ig-latn", "si", "ne", "ro", "zu", "hr", "el", "hu", "cs",
    "he", "sv",
]

#: Where their spelling differs from ours. Everything else is identical.
OURS_FOR = {"en-us": "en", "zh-hans": "zh", "yo-latn": "yo", "ig-latn": "ig"}

#: Which listing field each export row carries, by its Field name.
FIELD_KEYS = {"Title": "name", "Description": "description"}


def our_code(store_code: str) -> str:
    return OURS_FOR.get(store_code, store_code)


def build(export: pathlib.Path, out: pathlib.Path) -> int:
    rows = list(csv.reader(io.StringIO(export.read_text(encoding="utf-8-sig"))))
    if not rows or rows[0][:1] != ["Field"]:
        raise SystemExit(f"{export} does not look like a Partner Center export "
                         f"(first cell is {rows[0][:1] if rows else 'empty'!r})")

    header = rows[0]
    if len(header) < 5:
        raise SystemExit("The export has no language column at all.")
    # Everything before the first language is structural and is copied through
    # untouched: Field, ID, Type and `default`. Rebuilding those by hand is how
    # an import gets rejected for a reason nothing explains.
    lead = header[:4]

    translations, missing = {}, []
    for store_code in STORE_LANGUAGES:
        path = ADDON / f"{our_code(store_code)}.json"
        if not path.exists():
            missing.append(store_code)
            continue
        translations[store_code] = json.loads(path.read_text(encoding="utf-8"))

    if missing:
        raise SystemExit(
            f"No translation for {len(missing)} Store language(s): "
            f"{', '.join(missing)}. Run tools/translate_addon_listing.py first.")

    ours_spare = sorted({p.stem for p in ADDON.glob("*.json")}
                        - {our_code(c) for c in STORE_LANGUAGES})
    if ours_spare:
        print(f"note: {len(ours_spare)} locale(s) we translate have no Store "
              f"column and are skipped: {', '.join(ours_spare)}")

    out_rows = [lead + STORE_LANGUAGES]
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue          # the export pads with hundreds of blank rows
        field = row[0]
        key = FIELD_KEYS.get(field)
        if key is None:
            # An unrecognised field (the logo) is carried through with its
            # existing value repeated, rather than blanked. A blank cell in an
            # import is an instruction to CLEAR the field.
            existing = row[4] if len(row) > 4 else ""
            out_rows.append(row[:4] + [existing] * len(STORE_LANGUAGES))
            continue
        out_rows.append(row[:4] + [translations[c][key] for c in STORE_LANGUAGES])

    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(out_rows)

    print(f"{out}")
    print(f"  {len(out_rows) - 1} field row(s), {len(STORE_LANGUAGES)} languages")
    for row in out_rows[1:]:
        if row[0] in FIELD_KEYS:
            longest = max(len(c) for c in row[4:])
            print(f"  {row[0]:<12} longest {longest} characters")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=pathlib.Path,
                        help="a FRESH export from the add-on's submission")
    parser.add_argument("-o", "--out", type=pathlib.Path,
                        default=pathlib.Path("addon-listings-import.csv"))
    args = parser.parse_args(argv)
    if not args.export.exists():
        raise SystemExit(f"Not found: {args.export}")
    return build(args.export, args.out)


if __name__ == "__main__":
    sys.exit(main())
