"""Which screenshot sits in which Partner Center slot. ONE definition.

WHY THIS FILE EXISTS
--------------------
Two tools need the same answer and had their own copies of it:

  * `build_listing_csv.py` writes `DesktopScreenshotCaptionN`
  * `build_screenshot_folder.py` writes the localised image into slot N

If they disagree, the caption describes a different picture from the one
beside it, in every language at once. On 2026-09-09 they did disagree — both
assumed slot order matched file order, and the live listing did not:

    slot 1  01-shortlist      caption right by luck
    slot 2  04-understood     caption said "When a rule reaches too far"
    slot 3  03-board          caption right by luck
    slot 4  05-calibration    caption said "It reads your CVs"
    slot 5  02-needs-review   caption said "It learns your judgement"
    slot 6  06-rules          caption right by luck

Three of six wrong across forty-seven languages, and nothing in either tool
could have noticed: an asset id is opaque, so the only way to know what is in
a slot is to LOOK AT THE IMAGE. Established by opening each of the six asset
URLs in the browser and reading the screen in it.

TRANSLATED CAPTIONS STAY IN FILE ORDER. `store/listing/<loc>.json` holds
`screenshot_captions` as `01`…`06`, which is the order a human writing them
thinks in and the order the renderer produces. This module is the only place
that knows how those map onto slots, so re-ordering images in Partner Center
means editing one list here and rebuilding — not re-translating anything.

IF THE ORDER IS CHANGED IN PARTNER CENTER, THIS MUST BE UPDATED. There is no
way to derive it: re-export, open the six asset URLs, and write down what you
see. `tools/verify_listing_slots.py` prints the URLs to make that quick.
"""
from __future__ import annotations

#: Partner Center slot number -> screenshot stem, read off the live listing
#: on 2026-09-09. Index 0 is slot 1.
SLOT_ORDER = [
    "01-shortlist",
    "04-understood",
    "03-board",
    "05-calibration",
    "02-needs-review",
    "06-rules",
]

#: The order a caption list is written in, which is the order the renderer
#: emits and the order `store/listing/<loc>.json` stores.
FILE_ORDER = [
    "01-shortlist",
    "02-needs-review",
    "03-board",
    "04-understood",
    "05-calibration",
    "06-rules",
]


def caption_index_for_slot(slot: int) -> int:
    """0-based index into a `screenshot_captions` list, for 1-based slot N."""
    return FILE_ORDER.index(SLOT_ORDER[slot - 1])


def stem_for_slot(slot: int) -> str:
    """The screenshot file that belongs in 1-based slot N."""
    return SLOT_ORDER[slot - 1]


def slots() -> list[tuple[str, str]]:
    """`(stem, "DesktopScreenshotN")` pairs, in slot order."""
    return [(stem, f"DesktopScreenshot{i}")
            for i, stem in enumerate(SLOT_ORDER, 1)]


assert sorted(SLOT_ORDER) == sorted(FILE_ORDER), (
    "every screenshot must appear in both orders exactly once")
