"""The Microsoft Store ADD-ON listing, in every language the app supports.

    python tools/translate_addon_listing.py            # fill what is missing
    python tools/translate_addon_listing.py --force    # redo them all
    python tools/translate_addon_listing.py --check    # measure, call nothing

WHY THIS IS NOT `translate_listing.py`
--------------------------------------
The app listing has no length limits worth worrying about. This one has two
HARD caps that Partner Center enforces at save:

    Product name   100 characters
    Description    200 characters

The English description is already 169 of its 200. Translations routinely run
twenty to thirty per cent longer than English — German and Finnish especially —
so a straight translation of this text overflows, and Partner Center rejects it
at the point somebody is pasting the fiftieth language by hand.

So this does not translate and hope. It translates, MEASURES, and re-asks with
the measured overshoot quoted back, up to a few times; and `--check` re-measures
everything already on disk without calling anything, so the caps are verifiable
after the fact rather than only at generation time. A file that is over the cap
is a file that will be rejected, and it is far cheaper to know here.

SHORTENING IS THE TRANSLATOR'S JOB, NOT A TRUNCATION. Cutting a sentence at 200
bytes produces a description ending mid-word in a language nobody here reads.
The model is asked for a shorter rendering that keeps the same three facts:
what it unlocks, that it renews monthly until cancelled, and that the customer
still needs their own Anthropic key. That last one is the one that must never
be dropped to save room — it is the disclosure that stops the subscription
being mis-sold.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES  # noqa: E402
from tools.translate_listing import MODEL, client  # noqa: E402

ADDON = pathlib.Path(__file__).resolve().parents[1] / "store" / "listing-addon"

#: Partner Center's own limits, read off the add-on listing form.
LIMITS = {"name": 100, "description": 200}

#: How many times to go back with the measured overshoot before giving up. A
#: failure is reported rather than truncated: a description cut mid-word in a
#: language nobody here reads is worse than a missing one, because it looks
#: finished.
ATTEMPTS = 4


def over(entry: dict) -> dict:
    """Which fields are over, and by how much. Empty when the entry is fine."""
    return {field: len(entry[field]) - cap
            for field, cap in LIMITS.items()
            if field in entry and len(entry[field]) > cap}


PROMPT = """Translate this Microsoft Store add-on listing into {language} ({code}).

It sells a monthly subscription to Dawnlist, a job-search assistant for
Windows. The audience is an individual looking for work, not a business.

    name:        {name}
    description: {description}

HARD LIMITS, enforced by the Store and not negotiable:
    name         at most {name_cap} characters
    description  at most {description_cap} characters

If a natural translation does not fit, write a SHORTER one rather than a
truncated one. Keep these three facts in the description, in this order of
importance:
  1. the customer still needs their own Anthropic API key;
  2. it renews monthly until cancelled;
  3. what the subscription unlocks.
Drop detail from 3 before 2, and from 2 before 1. Never drop 1.

"Dawnlist" and "Anthropic" are names and stay as they are.

Answer with JSON only: {{"name": "...", "description": "..."}}"""


def ask(cl, code: str, language: str, src: dict, note: str = "") -> dict:
    message = PROMPT.format(
        language=language, code=code, name=src["name"],
        description=src["description"],
        name_cap=LIMITS["name"], description_cap=LIMITS["description"])
    if note:
        message += "\n\n" + note
    reply = cl.messages.create(
        # 16000 to match translate_listing.py, which measured Kannada and
        # Punjabi coming back EMPTY at 4000 - the caller then failed on
        # json.loads of an empty string, which reads like a parse bug.
        model=MODEL, max_tokens=16000,
        messages=[{"role": "user", "content": message}])
    # JOIN THE TEXT BLOCKS, never content[0]. The first block can be a
    # ThinkingBlock, which has no .text at all - taking index zero raises an
    # AttributeError that looks like an SDK problem and is not one.
    text = "".join(b.text for b in reply.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)


def translate_one(cl, code: str, language: str, src: dict) -> dict | None:
    note = ""
    for attempt in range(ATTEMPTS):
        entry = ask(cl, code, language, src, note)
        long = over(entry)
        if not long:
            return entry
        # The overshoot is quoted back rather than "make it shorter", because a
        # model asked vaguely tends to shave a word and still miss.
        note = ("Your previous answer was too long. "
                + "; ".join(f"{f} was {len(entry[f])} characters, "
                            f"{n} over the {LIMITS[f]} limit"
                            for f, n in long.items())
                + ". Write a shorter one that still keeps the Anthropic key fact.")
        print(f"    {code}: {long} — asking again ({attempt + 2}/{ATTEMPTS})")
    return None


def check() -> int:
    """Measure every file on disk. Calls nothing, costs nothing."""
    src = json.loads((ADDON / f"{DEFAULT_LOCALE}.json").read_text(encoding="utf-8"))
    print(f"limits: name {LIMITS['name']}, description {LIMITS['description']}")
    print(f"english: name {len(src['name'])}, "
          f"description {len(src['description'])}")
    bad, seen = [], 0
    for path in sorted(ADDON.glob("*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        seen += 1
        long = over(entry)
        if long:
            bad.append((path.stem, long))
    for code, long in bad:
        print(f"  OVER  {code}: {long}")
    print(f"{seen} file(s), {len(bad)} over the limit")
    return 1 if bad else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="redo languages that already have a file")
    parser.add_argument("--only", help="comma-separated locale codes")
    parser.add_argument("--check", action="store_true",
                        help="measure what is on disk and call nothing")
    args = parser.parse_args(argv)

    if args.check:
        return check()

    ADDON.mkdir(parents=True, exist_ok=True)
    source = ADDON / f"{DEFAULT_LOCALE}.json"
    if not source.exists():
        raise SystemExit(f"No source listing at {source}")
    src = json.loads(source.read_text(encoding="utf-8"))
    if over(src):
        raise SystemExit(f"The ENGLISH source is already over: {over(src)}")

    only = {c.strip() for c in args.only.split(",")} if args.only else None
    cl = client()
    failed = []
    for code, language, _native in SUPPORTED_LOCALES:
        if code == DEFAULT_LOCALE or (only and code not in only):
            continue
        path = ADDON / f"{code}.json"
        if path.exists() and not args.force:
            continue
        entry = translate_one(cl, code, language, src)
        if entry is None:
            failed.append(code)
            print(f"  FAILED {code}: could not fit the limits in {ATTEMPTS} tries")
            continue
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"  {code}: name {len(entry['name'])}, "
              f"description {len(entry['description'])}")

    if failed:
        print(f"\n{len(failed)} language(s) unwritten: {', '.join(failed)}")
        return 1
    return check()


if __name__ == "__main__":
    sys.exit(main())
