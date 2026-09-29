"""Generate optimised LinkedIn boolean search strings from Dawnlist queries.

LinkedIn's job search bar supports a subset of boolean operators:
  - "quoted phrase" for exact match
  - OR between alternatives
  - NOT to exclude terms
  - (parentheses) to group

AND is implicit between groups — LinkedIn treats a space as AND.
Nesting depth is limited to one level of parentheses.

The output is a copy-paste string the user drops into LinkedIn's search bar.
It is NOT an API query and has nothing to do with the Job Library API.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.feed.base import SearchQuery


@dataclass(frozen=True)
class LinkedInQuery:
    label: str
    search_string: str
    location_hint: str


def _quote(term: str) -> str:
    """Quote a multi-word term; single words need no quotes on LinkedIn."""
    term = term.strip()
    if not term:
        return ""
    if " " in term:
        return f'"{term}"'
    return term


def _or_group(terms: list[str]) -> str:
    quoted = [_quote(t) for t in terms if t.strip()]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return "(" + " OR ".join(quoted) + ")"


def build_search_string(query: SearchQuery) -> str:
    """One LinkedIn boolean search string from a Dawnlist SearchQuery."""
    parts: list[str] = []

    if query.titles:
        parts.append(_or_group(query.titles))

    if query.description_keywords:
        parts.append(_or_group(query.description_keywords))

    if query.exclude_title_terms:
        for term in query.exclude_title_terms:
            term = term.strip()
            if term:
                parts.append(f"NOT {_quote(term)}")

    return " ".join(p for p in parts if p)


def build_location_hint(query: SearchQuery) -> str:
    """A human-readable location string for the LinkedIn location filter."""
    locs: list[str] = []
    if query.cities:
        locs.extend(query.cities)
    if query.countries:
        locs.extend(query.countries)
    return ", ".join(locs) if locs else ""


def from_queries(queries: list[SearchQuery]) -> list[LinkedInQuery]:
    """Convert all enabled Dawnlist queries to LinkedIn search strings."""
    results: list[LinkedInQuery] = []
    for q in queries:
        search = build_search_string(q)
        if not search:
            continue
        results.append(LinkedInQuery(
            label=q.label,
            search_string=search,
            location_hint=build_location_hint(q),
        ))
    return results
