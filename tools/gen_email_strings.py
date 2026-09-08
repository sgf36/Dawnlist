"""Translate the licence email into every language the application ships.

    python tools/gen_email_strings.py --dry-run     # what it would do, free
    python tools/gen_email_strings.py --only fr,de  # a few
    python tools/gen_email_strings.py               # everything missing
    python tools/gen_email_strings.py --force       # redo them all

Writes `server/dawnlist-feed-worker/src/email-strings.js`. Edit ENGLISH below
and re-run; never hand-edit the generated file, because the next run overwrites
it.

WHY THE SAME FIFTY AND NOT A SHORTER LIST
-----------------------------------------
The application ships in fifty languages. Somebody who chose Dawnlist in
Japanese, read a Japanese site and set up a Japanese application, and then
receives the one email that actually unlocks it in English, has been told the
translation was decoration. The list is read from `app.i18n.LOCALE_CODES` at
run time rather than copied here, so the two cannot drift.

WHAT THIS DOES DIFFERENTLY FROM `translate_catalog.py`
------------------------------------------------------
That tool fills the app's UI catalogues, where strings are labels of two or
three words. These are sentences a buyer reads once, at the moment they are
most likely to be confused, so the notes below carry more context than a UI
key needs — particularly the two that are easy to render wrongly:

  * "licence key" must not become a word for a software *licence agreement*;
  * the Anthropic key sentence must not imply Dawnlist supplies the key, or
    that the buyer is being charged twice by us.

Placeholder parity is checked before anything is written: a translation that
loses `{count}` renders a literal brace to a paying customer.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "server" / "dawnlist-feed-worker" / "src" / "email-strings.js"

MODEL = "claude-sonnet-5"

#: Scripts written right to left. The email sets `dir` from this, on the
#: document rather than on a div — set it lower and the table layout still
#: lays out left to right and the mail reads as though never translated.
RTL = ["ar", "fa", "he", "ur"]

ENGLISH = {
    "subject": "Your Dawnlist licence key",
    "preheader": "Paste this into Settings to unlock the application.",
    "heading": "Your Dawnlist licence key",
    "thanks": "Thank you for subscribing. Here is the key that unlocks the application.",
    "to_use_heading": "To use it",
    "to_use_body": (
        "Open Dawnlist, go to Settings, and paste the key into the licence "
        "field. It works on every computer you own, and on both the Windows "
        "and macOS editions."
    ),
    "allowance": "Your plan covers up to {count} postings a day.",
    "own_key": (
        "You will also need an Anthropic API key of your own — Dawnlist reads "
        "postings and drafts messages on your account rather than ours, so "
        "your CV and your correspondence never reach our servers. The "
        "application walks you through adding it during setup."
    ),
    "not_installed": "Haven't installed it yet? Everything is at",
    "reply_note": "If anything does not work, reply to this message — it reaches a person.",
    "footer": "Dawnlist — a product of Spencer Fields Software.",
}

NOTES = {
    "subject": "An email subject line. Keep it short; it is read in a crowded inbox.",
    "preheader": "The preview snippet shown beside the subject. 'Settings' is the name of a screen in the application and should match the app's own translation of that word.",
    "heading": "The heading inside the email. May be identical to the subject.",
    "thanks": "'The key' means the licence key that follows. Warm but not effusive.",
    "to_use_heading": "A short bold heading introducing the instructions. Two or three words.",
    "to_use_body": "'Settings' and 'licence field' name things in the application. 'Licence key' is a CODE the person pastes, NEVER a licence agreement or terms document — if your language distinguishes these, choose the code sense. 'Every computer you own' means the licence is not tied to one machine.",
    "allowance": "{count} is a number, already formatted with thousands separators. Keep the placeholder exactly. 'Postings' means job adverts.",
    "own_key": "CRITICAL. This must not imply that Dawnlist supplies the Anthropic key, nor that we charge for it. The person obtains and pays for their own key, directly with Anthropic. The point of the sentence is reassurance about privacy: their CV never reaches our servers.",
    "not_installed": "A short question followed by a link, which is appended after this text. End so that a URL reads naturally after it.",
    "reply_note": "Reassurance that a reply is read by a human, not a no-reply address.",
    "footer": "A footer line. 'Spencer Fields Software' is a business name and is NEVER translated. 'Dawnlist' is a product name and is never translated.",
}

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def load_locales() -> list[str]:
    from app.i18n import LOCALE_CODES

    return [c for c in LOCALE_CODES if c != "en"]


def existing() -> dict:
    """What the generated file already holds, so a run can fill only gaps."""
    if not OUT.exists():
        return {}
    text = OUT.read_text(encoding="utf-8")
    match = re.search(r"export const EMAIL_STRINGS = (\{.*?\n\});", text, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def translate(client, code: str) -> dict:
    notes = "\n".join(f"  {k}: {v}" for k, v in NOTES.items())
    prompt = (
        f"Translate the values of this JSON object into the language with IETF "
        f"code '{code}', for a transactional email sent to somebody who has "
        f"just paid for a desktop application.\n\n"
        f"Return ONLY the JSON object, same keys, translated values. No "
        f"commentary, no code fence.\n\n"
        f"Per-key context, which matters more than the words:\n{notes}\n\n"
        f"Rules:\n"
        f"- Keep any {{placeholder}} exactly as written.\n"
        f"- 'Dawnlist', 'Anthropic', 'Windows', 'macOS' and 'Spencer Fields "
        f"Software' are names and are never translated.\n"
        f"- Use the register a competent company uses with a paying customer: "
        f"plain, warm, not marketing copy and not officialese.\n\n"
        f"{json.dumps(ENGLISH, ensure_ascii=False, indent=2)}"
    )
    reply = client.messages.create(
        model=MODEL, max_tokens=2000,
        messages=[{"role": "user", "content": prompt}])
    body = "".join(b.text for b in reply.content if b.type == "text").strip()
    body = re.sub(r"^```(?:json)?\s*\n", "", body)
    body = re.sub(r"\n```\s*$", "", body)
    out = json.loads(body)

    missing = set(ENGLISH) - set(out)
    if missing:
        raise ValueError(f"{code}: missing keys {sorted(missing)}")
    for key, english in ENGLISH.items():
        want = set(PLACEHOLDER.findall(english))
        got = set(PLACEHOLDER.findall(out[key]))
        if want != got:
            # A dropped placeholder renders a literal brace to a paying
            # customer, in a language nobody here can proofread.
            raise ValueError(f"{code}.{key}: placeholders {sorted(want)} became "
                             f"{sorted(got)}")
    return out


def write(catalogues: dict) -> None:
    ordered = {k: catalogues[k] for k in sorted(catalogues)}
    body = json.dumps(ordered, ensure_ascii=False, indent=2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "/**\n"
        " * The licence email's wording, in every language the application ships.\n"
        " *\n"
        " * GENERATED by tools/gen_email_strings.py — edit the English there and\n"
        " * re-run. Hand edits here are overwritten by the next run.\n"
        " *\n"
        " * A language with no entry falls back to English, so a partial run\n"
        " * degrades to the previous behaviour rather than to blank paragraphs.\n"
        " */\n"
        f"export const EMAIL_STRINGS = {body};\n\n"
        "/** Scripts written right to left. The shell sets `dir` from this. */\n"
        f"export const RTL = new Set({json.dumps(RTL)});\n\n"
        "export function strings(lang) {\n"
        "  return EMAIL_STRINGS[lang] || EMAIL_STRINGS.en;\n"
        "}\n",
        encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="comma-separated locale codes")
    ap.add_argument("--force", action="store_true",
                    help="retranslate locales that already have an entry")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    codes = load_locales()
    if args.only:
        wanted = {c.strip() for c in args.only.split(",")}
        codes = [c for c in codes if c in wanted]

    have = existing()
    have["en"] = ENGLISH          # always current, never translated
    todo = [c for c in codes if args.force or c not in have]

    print(f"{len(codes)} locales declared by the app; {len(todo)} to translate")
    if args.dry_run:
        for code in todo:
            print(f"  would translate {code}")
        return 0
    if not todo:
        write(have)
        print(f"nothing to do; rewrote {OUT.name} from existing entries")
        return 0

    import anthropic

    from app.core import api_key

    client = anthropic.Anthropic(api_key=api_key.require())

    failures = []
    for code in todo:
        try:
            have[code] = translate(client, code)
            print(f"  {code} ok")
        except Exception as exc:  # noqa: BLE001
            # One bad language must not lose the other forty-nine.
            failures.append(f"{code}: {type(exc).__name__}: {exc}")
            print(f"  {code} FAILED — {exc}")
        write(have)               # after each, so an interrupted run keeps work

    print(f"\nwrote {OUT} — {len(have)} locales")
    if failures:
        print("\nfailed, and left on English:")
        for line in failures:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
