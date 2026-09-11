"""The normalised Job schema — the only shape the app ever sees.

Every provider adapter returns this. The app speaks Spencer's own API shape and
never a provider's, which is what makes a provider swap a server-side config
change rather than an app release (handoff Part 0.3, Part 2.1b).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class Job:
    provider: str
    provider_job_id: str
    title: str
    company: str
    locations: tuple[str, ...] = ()
    description_text: str = ""
    posted_at: date | None = None
    salary: str | None = None
    url: str = ""
    # The provider's own structured tags (seniority, country, contract type…).
    # NEVER consulted by the screen: spec 6.8, metadata never outranks the
    # description. Two readers, both bounded. The scope gates in
    # `app/core/search_scope.py` may only REMOVE a posting whose own tag
    # contradicts what the user stated, and keep every posting whose tag is
    # blank. The assessment is SHOWN country and contract type as labelled
    # lines, because a brief's hard constraints are written in those terms —
    # and its rules say the description wins wherever it disagrees with them.
    raw_criteria: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> tuple[str, str]:
        """Rule 9 / spec 6.6: dedup is (provider, provider_job_id). Exact."""
        return (self.provider, self.provider_job_id)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["locations"] = list(self.locations)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return d


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9 ]")


#: Corporate suffixes that vary between sources for the same employer.
_COMPANY_NOISE = re.compile(
    r"\b(ltd|limited|llc|inc|incorporated|plc|gmbh|bv|nv|sa|ag|srl|pty|pte|"
    r"holdings|group|international)\b")

#: Words carrying no distinguishing weight in a job title.
_TITLE_STOPWORDS = frozenset({"the", "of", "and", "for", "a", "an", "at",
                              "in", "to", "on", "with"})


def _singular(word: str) -> str:
    """Conservative de-pluralisation.

    Measured in P0: a single plural cost a real hit — the live title was
    "Analyst, Investment and Portfolio Oversight" while the harness searched
    "analyst investments portfolio". At least one false negative in six came
    from a naive matcher.

    Deliberately conservative: only a trailing bare 's' is stripped, and only
    on words long enough that it is unlikely to be part of the stem, so
    "analysis" and "business" survive intact. Over-stemming would collapse
    genuinely different roles, which is the worse error.
    """
    if len(word) > 4 and word.endswith("s") and not word.endswith(
            ("ss", "us", "is", "as", "os")):
        return word[:-1]
    return word


def company_key(company: str) -> str:
    """An employer's name with case, punctuation, spacing and corporate
    suffixes normalised away — the company half of `name_key`.

    For telling whether two names are the SAME employer. Everything that
    compares employers goes through this rather than a substring test,
    because a short name sits inside countless longer ones: "EY" is inside
    "Bentley" and "The Walt Disney Company", "GIC" inside "Logic".
    """
    norm = _WS.sub(" ", _PUNCT.sub(" ", (company or "").lower())).strip()
    return _WS.sub(" ", _COMPANY_NOISE.sub(" ", norm)).strip()


def name_key(company: str, title: str) -> str:
    """A *near*-duplicate detector. Never a dedup key.

    spec 6.6: name keys only ever FLAG a near-duplicate for a human judgement
    call — they never drop a row and never prove absence. Because the key can
    only RAISE a flag, being generous is safe: a false flag costs one judgement
    call, while a missed flag lets a duplicate surface twice or a relisting go
    unnoticed.

    Four normalisations, each from a measured failure:
      - whitespace collapsed, because historical keys collapse runs of spaces
        where a current normaliser preserves them, and an ampersand in a title
        was enough to cause a real miss;
      - corporate suffixes dropped — a company-name variant returned n=0 for a
        whole employer across 365 days;
      - title stopwords dropped and tokens sorted, so word order and connecting
        words cannot split one role into two keys;
      - plurals folded, per the measurement above.

    The company stays in the key and the title tokens stay in the key, so
    "same company, different role" still produces a DIFFERENT key and can never
    collapse — that remains the worse failure.
    """
    company_norm = company_key(company)

    title_norm = _WS.sub(" ", _PUNCT.sub(" ", (title or "").lower())).strip()
    tokens = sorted(_singular(w) for w in title_norm.split()
                    if w and w not in _TITLE_STOPWORDS)
    return f"{company_norm} :: {' '.join(tokens)}"
