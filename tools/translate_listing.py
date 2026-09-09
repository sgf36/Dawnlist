"""Translate the Microsoft Store LISTING copy into every app locale.

    .venv/Scripts/python tools/translate_listing.py --dry-run
    .venv/Scripts/python tools/translate_listing.py --only de,ja
    .venv/Scripts/python tools/translate_listing.py

NOT the app catalogue. `translate_catalog.py` does short UI strings; this does
long-form marketing prose, and the failure modes are different.

THE ONE THAT MATTERS: THE LISTING QUOTES A BUTTON IN THE APP.
The description tells the reader to open Settings and use "Report AI-generated
content". That button is itself translated, in
`app/resources/locales/<code>.json` under `settings.report_link`. If this file
translated the quotation independently, the listing would name a button that
does not exist in that language, and the Policy 11.16 reporting route becomes
unfindable for exactly the people who need it. So the app's own string is
INJECTED into the prompt as a required literal rather than left to the model,
and its absence from the result is reported.

NEGATIVE CLAIMS MUST NOT SOFTEN. "It never sends anything", "There is no
sending code in the app at all", "It does not scrape job boards". A translation
rendering never as rarely is not a style problem, it is Store Policy 10.1.1 --
metadata must not mislead.

ALL-CAPS HEADINGS ARE AN ENGLISH CONVENTION. Arabic, Hebrew, CJK, Devanagari
and others have no case at all. A heading must read as a heading in the
target's own convention; forcing capitals produces nothing or produces noise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES  # noqa: E402

LISTING = ROOT / "store" / "listing"
CATALOGUE = ROOT / "app" / "resources" / "locales"
MODEL = "claude-opus-5"

GUIDANCE = """You are translating the Microsoft Store listing for Dawnlist, a
paid desktop application that reads job feeds and drafts job-search
correspondence. The reader is a person deciding whether to install it.

NEVER TRANSLATE THESE. Copy them exactly as written:
  Dawnlist, Anthropic, Paddle, Microsoft Store,
  console.anthropic.com, Apps@spencerfields.com

CLAIMS ARE LOAD-BEARING. This is a store listing and it must not mislead.
Where the English says never, no, none or does not, the translation must be
equally absolute. Do not soften "It never sends anything" into "it does not
usually send"; do not turn "There is no sending code in the app at all" into
"there is very little". These are factual claims about what the software does.

DO NOT INVENT OR CONVERT MONEY. "a few pounds a month" and "a pound or two a
month" are deliberately vague small amounts describing a third party's bill.
Render them as an equally vague small amount that reads naturally. Do NOT
convert to another currency and do NOT substitute a figure.

HEADINGS. Lines in capitals are section headings, which is an English
convention. Render them as a heading reads in your language. If your script has
no case, do not attempt to imitate capitals.

STRUCTURE. Keep the paragraph and blank-line structure exactly. The Store
renders this as plain text, so blank lines are the only formatting there is.
Keep the em dashes.

VOCABULARY. Use the word your market actually uses for a CV or resume, and for
a job posting. "fifty languages" is a fact; keep the number.

Return ONLY a JSON object with exactly two keys: "description" (a string) and
"features" (an array of strings, the same length and order as the input)."""


def client():
    import os

    import anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()
    import keyring
    key = keyring.get_password("anthropic-api", "spencer")
    if not key:
        sys.exit("No Anthropic key: set ANTHROPIC_API_KEY or store one under "
                 "keyring service 'anthropic-api', account 'spencer'.")
    return anthropic.Anthropic(api_key=key)


def report_link(code: str) -> str | None:
    """The app's OWN translation of the button the listing quotes."""
    p = CATALOGUE / f"{code}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("settings.report_link")


def translate(cl, name: str, code: str, src: dict) -> dict | None:
    button = report_link(code)
    extra = ""
    if button:
        extra = (
            "\n\nTHE QUOTED BUTTON. The description contains the quoted string "
            '"Report AI-generated content". That is a button in the '
            f"application, and in {name} the application labels it exactly:\n\n"
            f"    {button}\n\n"
            "Use THAT wording, verbatim, inside the quotation marks. Do not "
            "translate the phrase yourself -- a listing naming a button by "
            "different wording than the app uses sends the reader looking for "
            "something that is not there.")

    resp = cl.messages.create(
        model=MODEL, max_tokens=16000,
        messages=[{"role": "user", "content":
                   f"Translate into {name} ({code}).\n\n{GUIDANCE}{extra}\n\n"
                   f"{json.dumps(src, ensure_ascii=False, indent=2)}"}])
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        out = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"not JSON: {exc}")
        return None
    if not isinstance(out.get("description"), str) or not out["description"].strip():
        print("empty description")
        return None
    got = out.get("features")
    if not isinstance(got, list) or len(got) != len(src["features"]):
        print(f"features {len(got or [])} != {len(src['features'])}")
        return None
    if button and button not in out["description"]:
        out["_warning_button_wording_missing"] = button
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated locale codes")
    ap.add_argument("--force", action="store_true",
                    help="retranslate locales that already have a file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    LISTING.mkdir(parents=True, exist_ok=True)
    src = json.loads((LISTING / f"{DEFAULT_LOCALE}.json").read_text(encoding="utf-8"))
    wanted = set(args.only.split(",")) if args.only else None

    todo = []
    for code, name, _native in SUPPORTED_LOCALES:
        if code == DEFAULT_LOCALE or (wanted and code not in wanted):
            continue
        if (LISTING / f"{code}.json").exists() and not args.force:
            continue
        todo.append((code, name))

    print(f"{len(todo)} locale(s) to translate")
    if args.dry_run:
        for code, name in todo:
            print(f"  {code} ({name})")
        return 0

    cl = client()
    ok = fail = warned = 0
    for code, name in todo:
        print(f"  {code:<3} {name:<22} ", end="", flush=True)
        out = translate(cl, name, code, src)
        if out is None:
            fail += 1
            continue
        warn = out.pop("_warning_button_wording_missing", None)
        (LISTING / f"{code}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        note = "  BUTTON WORDING MISSING" if warn else ""
        if warn:
            warned += 1
        print(f"{len(out['description']):>6} chars{note}")
        ok += 1

    print(f"\n{ok} written, {fail} failed, {warned} missing the app's button wording")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
