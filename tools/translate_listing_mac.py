"""Translate the MAC APP STORE listing copy into every Apple locale we offer.

    .venv/Scripts/python tools/translate_listing_mac.py --dry-run
    .venv/Scripts/python tools/translate_listing_mac.py --only de,ja
    .venv/Scripts/python tools/translate_listing_mac.py

NOT `translate_listing.py`. That one does the Microsoft Store copy, which says
"bought on our website" — a sentence that would fail Apple's guideline 3.1.1.
The two listings are different documents and `tools/mac_listing.py` says why.

THE CHARACTER LIMITS ARE THE WHOLE DIFFICULTY. A subtitle is 30 characters and
keywords are 100 INCLUDING the commas. English fits; German does not, and Apple
rejects the request rather than trimming. So a field that comes back too long
is sent back to be said SHORTER, up to three times, and a locale that still
will not fit is reported and left unwritten. Nothing here truncates: a subtitle
cut mid-word is worse than a shorter phrase that means the same thing, and a
keyword list cut mid-term leaves a fragment nobody searches for.

KEYWORDS ARE NOT A TRANSLATION OF THE ENGLISH KEYWORDS. They are the terms a
person in that market actually types, which is why the model is asked for terms
rather than for a translation, and why `store/listing/<loc>.json` already holds
a `search_terms` list from the Microsoft work — those are reused as the
starting point rather than invented twice.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.i18n import SUPPORTED_LOCALES  # noqa: E402
from tools.mac_listing import (LIMITS, MAC_LISTING, app_locales,  # noqa: E402
                               check, load, report_button)

MODEL = "claude-opus-5"
NAMES = {code: name for code, name, _native in SUPPORTED_LOCALES}

#: The fields with a tight length limit, asked for separately from the
#: description so a retry does not regenerate 3,900 characters of prose.
SHORT_FIELDS = ("subtitle", "promotional", "keywords")

GUIDANCE = """You are translating the MAC APP STORE product page for Dawnlist,
a paid Mac application that reads job feeds and drafts job-search
correspondence. The reader is a person deciding whether to subscribe.

NEVER TRANSLATE THESE. Copy them exactly as written:
  Dawnlist, Anthropic, Mac App Store, console.anthropic.com

CLAIMS ARE LOAD-BEARING. Where the English says never, no, none or does not,
the translation must be equally absolute. Do not soften "It never sends
anything" into "it does not usually send"; do not turn "There is no sending
code in the app at all" into "there is very little". These are factual claims
about what the software does, and a product page that misleads is rejected.

DO NOT INVENT OR CONVERT MONEY. "a few pounds a month" is a deliberately vague
small amount describing a third party's bill. Render it as an equally vague
small amount that reads naturally. Do NOT convert to another currency and do
NOT substitute a figure.

HEADINGS. Lines in capitals are section headings, which is an English
convention. Render them as a heading reads in your language. If your script has
no case, do not attempt to imitate capitals.

STRUCTURE. Keep the paragraph and blank-line structure exactly. The App Store
renders this as plain text, so blank lines are the only formatting there is.
Keep the em dashes.

VOCABULARY. Use the word your market actually uses for a CV or resume, and for
a job posting. "fifty languages" is a fact; keep the number.

"""

DESCRIPTION_TASK = """THE FIELD

  "description"   the long product description. Keep every paragraph.

LENGTH. Apple's hard limit is {description} characters and the English source is
about {source} — so there is very little room. Aim for ROUGHLY THE SAME LENGTH
AS THE ENGLISH. Do not expand: no added connectives, no explanatory asides, no
"in other words". A translation that runs a quarter longer than its source is
the normal outcome here and it does not fit, so prefer the shorter of two
faithful phrasings every time.

Return ONLY a JSON object with exactly one key, "description", a string."""

SHORT_TASK = """THE THREE SHORT FIELDS. These sit at the top of the product page
and each has a hard length limit, so say what fits rather than what is
complete.

  "subtitle"     about {subtitle} characters. It sits under the app name. A
                 phrase, not a sentence, and no full stop.
  "promotional"  about {promotional} characters. One or two sentences saying
                 what the app does for the reader.
  "keywords"     about {keywords} characters, and the commas count. A
                 comma-separated list of search terms a person in your market
                 would actually type when looking for an app like this. No
                 spaces after the commas — every space costs a character that
                 could have been part of a term. Do not repeat words already in
                 the app name or the subtitle; Apple indexes those separately.
                 These are search terms, NOT a translation of the English list.

Do not count characters — write naturally at roughly those lengths. Anything
that does not fit comes back to you with the measurement.

Here is the English product description for context. Do NOT translate it; it is
only there so the three short fields describe the same app:

{english}

Return ONLY a JSON object with exactly those three keys, each a string."""


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


def existing_terms(code: str) -> str:
    """The Microsoft listing's translated `search_terms`, as a starting point."""
    p = ROOT / "store" / "listing" / f"{code}.json"
    if not p.exists():
        return ""
    terms = json.loads(p.read_text(encoding="utf-8")).get("search_terms") or []
    if not terms:
        return ""
    return ("\n\nALREADY TRANSLATED FOR THE MICROSOFT STORE, for this same app "
            "and this same market. Start from these, keep the ones that are "
            "right, and cut or shorten until the whole list fits:\n\n    "
            + ", ".join(terms))


def ask(cl, prompt: str, budget: int) -> dict:
    """One request. Returns the JSON object, or raises saying which fault it was.

    THINKING IS OFF, DELIBERATELY, AND NOT AS A COST SAVING. The first version
    of this asked the model to count characters before answering — a token-level
    task it cannot do by inspection — and German came back with
    `stop_reason: max_tokens`, zero text blocks, and 32,000 output tokens ALL
    of them thinking, spent reasoning about a 30-character subtitle. Measuring
    is the caller's job and always was: `check()` counts, and `one()` sends the
    measurement back. So the model is asked to write, not to count.

    Streamed because a description-length reply can otherwise exceed the
    non-streaming request timeout.
    """
    with cl.messages.stream(
            model=MODEL, max_tokens=budget, thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}]) as stream:
        reply = stream.get_final_message()
    text = "".join(b.text for b in reply.content if b.type == "text")
    if not text.strip():
        # Say which of the two it was. A truncated reply and a refused one look
        # identical from json.loads, which reports "Expecting value: line 1
        # column 1" and names neither.
        raise RuntimeError(f"no text (stop reason: {reply.stop_reason}, "
                           f"{reply.usage.output_tokens} output tokens)")
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


def preamble(code: str) -> str:
    button = report_button(code)
    return (f"Translate into {NAMES[code]} ({code}).\n\n" + GUIDANCE
            + "\n\nTHE QUOTED BUTTON. The description contains the quoted "
              '"Report AI-generated content". That is a button in the '
              f"application, and in {NAMES[code]} the application labels it "
              f"exactly:\n\n    {button}\n\n"
              "Use THAT wording, verbatim, inside the quotation marks. Do not "
              "translate the phrase yourself — a product page naming a button "
              "by different wording than the app uses sends the reader looking "
              "for something that is not there.")


def one(cl, code: str, src: dict) -> tuple[dict | None, list[str]]:
    """Translate one locale: description, then the short fields, each until it fits.

    TWO CALLS, NOT ONE. Both halves need retrying against a measured length, but
    they must not share a retry: asking for all four together meant a subtitle
    two characters over regenerated a 3,900-character description that was
    already right — paid for again, and different the second time for no reason
    anybody asked for.

    THE DESCRIPTION NEEDS RETRYING TOO, which the first version assumed it did
    not. The English runs 3,917 characters against Apple's 4,000 limit, and
    German came back at 4,857: translations of this text run a quarter longer
    than the source far more often than they run shorter, so the English that
    fits with 83 characters to spare is exactly the copy that overflows in
    German, Finnish and Greek. It is condensed on the retry rather than cut —
    losing the sending boundary or the API-key disclosure to save characters
    would make the page misleading, which is the one thing it must not be.
    """
    # 24000 is for the description alone. Devanagari, Thai and Greek cost
    # several tokens per character, so a budget that is generous for French
    # truncates for Hindi.
    out: dict = {}
    complaint = ""
    for attempt in range(3):
        try:
            got = ask(cl, preamble(code) + "\n\n"
                      + DESCRIPTION_TASK.format(
                          source=len(src["description"]), **LIMITS)
                      + complaint
                      + "\n\n" + json.dumps({"description": src["description"]},
                                            ensure_ascii=False, indent=2), 24000)
        except Exception as exc:                    # noqa: BLE001
            return None, [f"{code}: description: {exc}"]
        out = {"description": (got.get("description") or "").strip()}
        if 0 < len(out["description"]) <= LIMITS["description"]:
            break
        # AIM BELOW THE LIMIT, LOWER EACH TIME, AND SAY A NUMBER.
        #
        # Repeating the ceiling does not work. Malay was told "4000" six times
        # across two runs and answered 4191, 4209 — it lands a consistent 5%
        # over whatever ceiling it is given, because a ceiling is a thing to
        # approach. A TARGET is a thing to hit, so each attempt asks for a
        # target further below the limit (10%, then 20%) and the overshoot
        # lands inside it.
        target = int(LIMITS["description"] * (0.9 if attempt == 0 else 0.8))
        complaint = (
            f"\n\nTHIS IS ATTEMPT {attempt + 2}. THE LAST ONE WAS "
            f"{len(out['description'])} characters, and the hard limit is "
            f"{LIMITS['description']}, so it was rejected.\n\n"
            f"AIM FOR {target} CHARACTERS THIS TIME — not {LIMITS['description']}, "
            f"which is the point of failure rather than the target. Say the "
            "same things in fewer words: shorter sentences, no connective "
            "padding, no restating in the second half of a sentence what the "
            "first half already said.\n\n"
            "DROP NOTHING. Every section must survive, and in particular the "
            "paragraph about the Anthropic API key, the statement that it "
            "never sends anything, and the route for reporting AI-generated "
            "content must all still be there in full.")
    else:
        return None, [f"{code}: description is {len(out['description'])}, "
                      f"limit {LIMITS['description']}, after 3 attempts"]

    task = SHORT_TASK.format(english=src["description"], **LIMITS)
    complaint = ""
    bad: list[str] = []
    for attempt in range(3):
        try:
            short = ask(cl, preamble(code) + "\n\n" + task
                        + existing_terms(code) + complaint + "\n\n"
                        + json.dumps({k: src[k] for k in SHORT_FIELDS},
                                     ensure_ascii=False, indent=2), 4000)
        except Exception as exc:                    # noqa: BLE001
            return None, [f"{code}: short fields: {exc}"]
        out.update({k: (short.get(k) or "").strip() for k in SHORT_FIELDS})
        bad = check(code, out)
        if not bad:
            return out, []
        over = [f'"{k}" came back at {len(out[k])} characters and the limit is '
                f'{LIMITS[k]}' for k in SHORT_FIELDS if len(out[k]) > LIMITS[k]]
        if not over:
            # Something other than length — an empty field, or a description
            # that does not quote the app's own button. Neither is fixed by
            # asking again for something shorter, so stop rather than spend
            # twice more.
            return None, bad
        complaint = (f"\n\nTHIS IS ATTEMPT {attempt + 2}. THE LAST ONE DID NOT "
                     "FIT:\n" + "\n".join("  " + o for o in over)
                     + "\nSay the same thing in fewer characters. Do not "
                       "truncate and do not leave a fragment; shorten the "
                       "wording.")
    return None, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated app locales")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="retranslate locales that already have a file")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    src = load("en")

    todo = []
    for code in app_locales():
        if only and code not in only:
            continue
        path = MAC_LISTING / f"{code}.json"
        if path.exists() and not args.force:
            problems = check(code, json.loads(path.read_text(encoding="utf-8")))
            if not problems:
                continue
            print(f"{code}: exists but is not usable — {problems[0]}")
        todo.append(code)

    print(f"{len(todo)} locale(s) to translate: {', '.join(todo) or 'none'}")
    if args.dry_run or not todo:
        return 0

    cl = client()
    failures = []
    for code in todo:
        out, bad = one(cl, code, src)
        if out is None:
            failures.extend(bad)
            print(f"  {code}  FAILED — {bad[0] if bad else 'unknown'}")
            continue
        (MAC_LISTING / f"{code}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"  {code}  subtitle {len(out['subtitle'])}, "
              f"promo {len(out['promotional'])}, "
              f"keywords {len(out['keywords'])}, "
              f"description {len(out['description'])}")

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for f in failures:
            print("  " + f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
