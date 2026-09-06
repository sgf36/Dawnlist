"""Generate the 48 outstanding locale catalogues from `en.json`.

NOT RUN AUTOMATICALLY — it calls the Anthropic API and therefore spends money.
Run it deliberately, once, and re-run only for locales that are missing or that
have gained keys.

    python tools/translate_catalog.py --dry-run        # what it would do, free
    python tools/translate_catalog.py --only fr,de,es  # a few
    python tools/translate_catalog.py                  # everything missing

Two things it does NOT do, both deliberate:

  * it never overwrites an existing catalogue unless --force is given, so a
    hand-corrected translation is not silently reverted by a later run;
  * it never invents keys. Only keys present in en.json are translated, so a
    catalogue can never drift ahead of the source.

The placeholder check is the important part: a translation that drops
`{count}` or `{reason}` renders as a KeyError at runtime, in front of the user,
in a language the developer cannot read. Every returned catalogue is verified
for placeholder parity before it is written.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES  # noqa: E402

LOCALES = Path(__file__).resolve().parents[1] / "app" / "resources" / "locales"
PLACEHOLDER = re.compile(r"\{(\w+)\}")

MODEL = "claude-sonnet-5"

PROMPT = """\
Translate this software UI catalogue into {language} ({code}).

Rules:
- Return ONLY a JSON object with exactly the same keys. No commentary.
- Preserve every {{placeholder}} token exactly as written, including its name.
  A dropped or renamed placeholder crashes the app in front of the user.
- These are UI strings for a job-search desktop app. Keep them short enough to
  fit a button or a column header.
- "Dawnlist" is a product name: do not translate it, but transliterate it if
  the target script is non-Latin.
- Use the register a professional desktop application would use in {language}.

Catalogue:
{catalogue}"""


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def verify(english: dict, translated: dict) -> list[str]:
    """Every problem with a returned catalogue, named."""
    problems = []
    missing = set(english) - set(translated)
    if missing:
        problems.append(f"missing keys: {sorted(missing)}")
    extra = set(translated) - set(english)
    if extra:
        problems.append(f"invented keys: {sorted(extra)}")
    for key in set(english) & set(translated):
        want, got = placeholders(english[key]), placeholders(translated[key])
        if want != got:
            problems.append(
                f"{key}: placeholders {sorted(want)} became {sorted(got)}")
    return problems


def translate(client, language: str, code: str, keys: dict) -> dict | None:
    """One request. Returns the catalogue, or None if it did not verify."""
    resp = client.messages.create(
        model=MODEL, max_tokens=16000,
        messages=[{"role": "user", "content": PROMPT.format(
            language=language, code=code,
            catalogue=json.dumps(keys, ensure_ascii=False, indent=2))}],
        output_config={"format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in keys},
                "required": list(keys),
                "additionalProperties": False,
            },
        }},
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def fill(english: dict, wanted: set | None, *, dry_run: bool) -> int:
    """Top up catalogues that are BEHIND the source.

    A catalogue can never drift ahead — the generator only ever emits keys that
    exist in en.json — but it drifts behind every time a string is added to the
    app after the catalogues were generated. That shows up as an English string
    in the middle of a translated screen, which looks like a bug in the
    translation rather than a missing key.
    """
    behind = {}
    for code, name, _native in SUPPORTED_LOCALES:
        if code == DEFAULT_LOCALE or (wanted and code not in wanted):
            continue
        path = LOCALES / f"{code}.json"
        if not path.exists():
            continue
        existing = json.loads(path.read_text("utf-8"))
        missing = {k: v for k, v in english.items() if k not in existing}
        if missing:
            behind[code] = (name, existing, missing)

    print(f"{len(behind)} catalogues are behind the source")
    if dry_run:
        for code, (name, _e, missing) in behind.items():
            print(f"  {code} ({name}): {len(missing)} keys")
        return 0
    if not behind:
        return 0

    import anthropic
    client = anthropic.Anthropic()

    failures = []
    for code, (name, existing, missing) in behind.items():
        translated = translate(client, name, code, missing)
        if translated is None:
            failures.append(f"{code}: unparseable response")
            continue
        problems = verify(missing, translated)
        if problems:
            failures.append(f"{code}: " + "; ".join(problems))
            continue
        existing.update(translated)
        # Written back in the SOURCE's key order, so a diff between two
        # catalogues is readable and a missing key is visible by position.
        ordered = {k: existing[k] for k in english if k in existing}
        (LOCALES / f"{code}.json").write_text(
            json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"  filled {code}.json (+{len(missing)})")

    for f in failures:
        print("  NOT filled: " + f, file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated locale codes")
    ap.add_argument("--force", action="store_true",
                    help="overwrite catalogues that already exist")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be generated; makes no API calls")
    ap.add_argument("--fill", action="store_true",
                    help="top up existing catalogues with keys added since "
                         "they were generated, instead of writing new ones")
    args = ap.parse_args()

    english = json.loads((LOCALES / f"{DEFAULT_LOCALE}.json").read_text("utf-8"))
    wanted = {c.strip() for c in args.only.split(",")} if args.only else None

    targets = []
    for code, name, _native in SUPPORTED_LOCALES:
        if code == DEFAULT_LOCALE:
            continue
        if wanted and code not in wanted:
            continue
        if (LOCALES / f"{code}.json").exists() and not args.force:
            continue
        targets.append((code, name))

    if args.fill:
        return fill(english, wanted, dry_run=args.dry_run)

    print(f"{len(english)} keys; {len(targets)} locales to generate")
    if args.dry_run:
        for code, name in targets:
            print(f"  would generate {code}.json ({name})")
        return 0
    if not targets:
        return 0

    import anthropic
    client = anthropic.Anthropic()

    failures = []
    for code, name in targets:
        prompt = PROMPT.format(language=name, code=code,
                               catalogue=json.dumps(english, ensure_ascii=False,
                                                    indent=2))
        resp = client.messages.create(
            model=MODEL, max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {k: {"type": "string"} for k in english},
                    "required": list(english),
                    "additionalProperties": False,
                },
            }},
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            translated = json.loads(text)
        except json.JSONDecodeError as e:
            failures.append(f"{code}: unparseable response ({e})")
            continue

        problems = verify(english, translated)
        if problems:
            # Never write a broken catalogue. A missing file falls back to
            # English cleanly; a corrupt one fails in front of the user.
            failures.append(f"{code}: " + "; ".join(problems))
            continue

        (LOCALES / f"{code}.json").write_text(
            json.dumps(translated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"  wrote {code}.json ({name})")

    if failures:
        print("\nNOT written:", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
