"""Translate the CONTENT inside store screenshots, not just the chrome.

`app/i18n.py` translates the application. That gets you German buttons around
English job titles and English AI verdicts, which reads as half-finished — and
is arguably wrong, because a German user's shortlist would contain German
postings judged in German. Easy-Post never hit this because its screenshots
show shipment numbers and carrier names, which stay English by design.

The verdicts matter most. The whole listing sells "it tells you WHY it set a
posting aside"; showing that reasoning in English on a German store page
undercuts the exact claim the screenshot exists to make.

KEYED BY THE ENGLISH STRING, deliberately. The alternative is inventing stable
ids for ~90 fixture strings and threading them through five renderers, which is
a lot of churn for throwaway demo data. Keying by the source string means a
renderer edit that changes a literal simply falls back to English rather than
rendering a stale translation under a stale key — the safe direction.

WHAT IS NOT TRANSLATED, and must not be:

  * COMPANY NAMES. They are invented proper nouns — Ashcombe Ridge Partners,
    Castellan Hotels. A proper noun does not translate, and a German reader
    seeing "Kastellan-Hotels" would think it a different company.
  * URLs, ids, bucket values, dates, salaries.
  * Anything already coming from `app/i18n.py`. That is the chrome, and it is
    translated by the application itself when the locale is set.

MISSING TRANSLATIONS FALL BACK TO ENGLISH AND SAY SO. `report_missing()` lists
what was asked for and not found, so a half-translated screenshot is visible
before it is published rather than after.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

_locale = "en"
_table: dict[str, str] = {}
_missing: set[str] = set()
_asked: set[str] = set()


def set_fixture_locale(code: str) -> str:
    """Load the fixture table for `code`. English is a no-op."""
    global _locale, _table, _missing, _asked
    _locale = code
    _missing = set()
    _asked = set()
    if code == "en":
        _table = {}
        return code
    path = FIXTURES / f"{code}.json"
    _table = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return code


def t(s: str | None) -> str | None:
    """The translation, or the English unchanged."""
    if not s or _locale == "en":
        return s
    _asked.add(s)
    got = _table.get(s)
    if got:
        return got
    _missing.add(s)
    return s


def tr_fields(obj, *fields):
    """A copy of a dataclass with the named fields translated."""
    import dataclasses
    if _locale == "en":
        return obj
    changes = {}
    for f in fields:
        v = getattr(obj, f, None)
        if isinstance(v, str) and v:
            changes[f] = t(v)
    return dataclasses.replace(obj, **changes) if changes else obj


def asked() -> set[str]:
    """Every English string this render actually looked up.

    Used to BUILD the English table from a real render rather than by grepping
    the renderers, so the table cannot drift from what is on screen.
    """
    return set(_asked)


def report_missing() -> list[str]:
    return sorted(_missing)
