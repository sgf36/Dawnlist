"""Translate the demo content inside store screenshots.

    .venv/Scripts/python tools/translate_fixtures.py de es fr hi ja zh

Rebuild the English table first, from a real render, so it cannot drift:

    .venv/Scripts/python tools/render_store.py --collect

WHY THE SCREENSHOT LANGUAGES ARE A SHORT LIST. Easy-Post ships seven captured
sets (en zh hi es fr de ja) and the other forty listing languages reference the
English images — documented as normal practice in its runbook, and something a
previous session mistook for a fault and "repaired" across thirty-six languages
that were already correct. Six new sets here is matching that, not falling
short of it.

It is also the safe size. Easy-Post's 40-language import sat on "Importing" and
never finished at 360 assets; the same machinery completed in minutes at 63.
Six locales times five screenshots is 30.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.i18n import SUPPORTED_LOCALES  # noqa: E402

FIXTURES = ROOT / "tools" / "fixtures"
MODEL = "claude-opus-5"

GUIDANCE = """These are the demo contents of screenshots for Dawnlist, a
job-search application. A person browsing the Microsoft Store in your language
will see them, so they must read as a real product in that market, not as
translated English.

WHAT THESE STRINGS ARE:
  - job titles, as a real posting in your market would word them
  - city names
  - one-line verdicts explaining why the app judged a posting a fit or not
  - screening terms a job seeker would type to rule postings out
  - short task reminders

TRANSLATE FOR THE MARKET, NOT WORD BY WORD. "Head of Asset Management" should
become the title that role actually carries in your language, not a literal
rendering. Same for the screening terms: use the words someone would really
type.

CITY NAMES take their usual form in your language: London, Rome, Paris,
Berlin, New York.

THE VERDICTS ARE THE POINT. The product's whole claim is that it tells you WHY
a posting was set aside. These must read as fluent, specific reasoning — a
stilted verdict makes the product look worse than an English one would.

KEEP THEM SHORT. They sit in table cells and are clipped if they grow. Aim at
or under the English length; never more than about 1.3x.

Return ONLY a JSON object mapping each English string to its translation.
Every key must appear exactly once, unchanged."""


def client():
    import os

    import anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()
    import keyring
    key = keyring.get_password("anthropic-api", "spencer")
    if not key:
        sys.exit("No Anthropic key.")
    return anthropic.Anthropic(api_key=key)


def name_of(code: str) -> str:
    for c, name, _native in SUPPORTED_LOCALES:
        if c == code:
            return name
    return code


def main() -> int:
    codes = sys.argv[1:]
    if not codes:
        sys.exit("usage: translate_fixtures.py de es fr hi ja zh")

    src = json.loads((FIXTURES / "en.json").read_text(encoding="utf-8"))
    english = sorted(src)
    print(f"{len(english)} fixture strings")

    cl = client()
    for code in codes:
        target = FIXTURES / f"{code}.json"
        print(f"  {code:<3} {name_of(code):<20} ", end="", flush=True)
        resp = cl.messages.create(
            model=MODEL, max_tokens=16000,
            messages=[{"role": "user", "content":
                       f"Translate into {name_of(code)} ({code}).\n\n{GUIDANCE}"
                       f"\n\n{json.dumps(english, ensure_ascii=False, indent=1)}"}])
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            out = json.loads(text)
        except json.JSONDecodeError as exc:
            print(f"not JSON: {exc}")
            continue
        missing = [k for k in english if not out.get(k)]
        if missing:
            # Partial tables are the failure mode that produces a
            # half-translated screenshot, which is worse than an English one.
            print(f"MISSING {len(missing)} key(s), not written")
            continue
        target.write_text(
            json.dumps({k: out[k] for k in english}, ensure_ascii=False, indent=2)
            + "\n", encoding="utf-8")
        print(f"{len(out)} strings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
