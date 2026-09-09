"""Redact what must not be public, before the repository goes public.

    .venv/Scripts/python tools/scrub_for_public.py --check
    .venv/Scripts/python tools/scrub_for_public.py

`--check` exits non-zero if a redacted value has come back. Adding a live
identifier to a public repository is easy to do by accident and impossible to
undo, because the history is public too.

THE LIST IS SHORT ON PURPOSE. A first draft of this script also redacted the
Worker URL, the Paddle price id and the developer's home directory, and every
one of those was wrong:

  * THE PADDLE PRICE AND PRODUCT IDS ARE FUNCTIONAL CONFIG.
    `PADDLE_PRICE_STANDARD` is a var in `wrangler.jsonc` that the Worker reads
    at runtime, and that file's own comment already reasoned it through: "A
    price id is a public identifier — it appears in the checkout URL."
    Redacting it breaks `wrangler deploy` and protects nothing.
  * THE WORKER URL is called by the shipped application on every run, so
    anyone who installs Dawnlist can read it off the wire. Removing it from
    source would be theatre and would break every runbook.
  * `sk-ant-` IN THE LOCALE CATALOGUES is a placeholder shown in an empty
    field, not a key.
  * THE DEVELOPER'S NAME in a path is not a secret — it is the same name on
    the public product website.
  * THE FEED PROVIDER'S NAME is publishable by Spencer's decision of
    2026-09-09. The older rule against naming it does not apply here.

Redacting things that are not secret trains everyone to ignore the output, and
the one entry below is the one that actually matters.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: (pattern, replacement, why)
#:
#: THESE MATCH A SHAPE, NOT A LITERAL, AND THAT IS NOT A STYLE CHOICE.
#: The first version listed the actual code as a literal. Then the history
#: rewrite ran, replaced that literal everywhere INCLUDING inside this file,
#: and left a rule reading "DL-XXXX-...  ->  DL-XXXX-..." — a guard that
#: matched only its own placeholder and could never fire again. It reported
#: "clean" while being incapable of finding anything.
#:
#: Matching the shape survives its own redaction and is strictly better
#: anyway: it catches the NEXT code somebody pastes in, not just this one.
RULES: list[tuple[str, str, str]] = [
    (r"DL-(?!XXXX)[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}",
     "DL-XXXX-XXXX-XXXX-XXXX",
     "A review code — 25 activations of a paid plan against real metered feed "
     "credits, and people scan public GitHub for key-shaped strings. Codes "
     "also reach the HISTORY, which a scrub of the working tree cannot fix."),
    (r"ntfset_(?!REDACTED)[0-9a-z]{26}",
     "ntfset_REDACTED",
     "A Paddle notification destination id. Easy-Post's leaked into this "
     "repository's runbook — another product's infrastructure that does not "
     "belong here at all."),
]

#: This file necessarily contains every pattern it searches for.
SKIP = {"tools/scrub_for_public.py"}

#: Codes that are DELIBERATELY code-shaped and deliberately not real. The
#: Worker's tests feed these to `handleRedeem` to prove it refuses them, so
#: redacting them would break the tests that prove refusal works.
#:
#: Listed individually rather than skipping `test/` wholesale, so that a REAL
#: code pasted into a test file still trips the guard. The whole point is to
#: catch the careless paste, and tests are where careless pastes live.
ALLOW = {
    "DL-ZZZZ-ZZZZ-ZZZZ-ZZZZ",
    "DL-BAD1-BAD1-BAD1-BAD1",
}

TEXT_SUFFIXES = {".md", ".py", ".ps1", ".json", ".yml", ".yaml", ".js",
                 ".jsonc", ".txt", ".xml", ".spec", ".sql", ".mjs"}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split("\n")
    for rel in out:
        rel = rel.strip()
        if not rel or rel in SKIP:
            continue
        p = ROOT / rel
        if p.suffix.lower() in TEXT_SUFFIXES and p.is_file():
            yield rel, p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report only; exit 1 if anything would change")
    args = ap.parse_args()

    findings: dict[tuple[str, str], int] = {}
    changed = 0

    for rel, path in tracked_files():
        try:
            s = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        original = s
        for pattern, repl, why in RULES:
            found = [m for m in re.findall(pattern, s) if m not in ALLOW]
            if not found:
                continue
            findings[(rel, why)] = findings.get((rel, why), 0) + len(found)
            s = re.sub(pattern,
                       lambda m: m.group(0) if m.group(0) in ALLOW else repl,
                       s)
        if s != original and not args.check:
            path.write_text(s, encoding="utf-8")
            changed += 1

    if not findings:
        print("clean — nothing to redact")
        return 0

    for (rel, why), n in findings.items():
        print(f"  {rel}  ({n})\n      {why}")

    if args.check:
        print(f"\n{len(findings)} finding(s). Run without --check to redact.")
        return 1

    print(f"\n{changed} file(s) rewritten.")
    print()
    print("HEAD IS NOW CLEAN. THE HISTORY IS NOT.")
    print("The review code appears in 2 earlier commits, and making a")
    print("repository public publishes every commit. Rewrite history before")
    print("changing visibility, or rotate the code and accept the exposure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
