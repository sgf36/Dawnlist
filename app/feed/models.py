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
    # The provider's own structured tags (seniority, industry, …). Kept so the
    # UI can show them, NEVER consulted by the screen or the assessment: spec
    # 6.8, metadata never outranks the description.
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


def name_key(company: str, title: str) -> str:
    """A *near*-duplicate detector. Never a dedup key.

    spec 6.6: name keys only ever FLAG a near-duplicate for a human judgement
    call. Two sub-rules are baked in here:
      - whitespace is collapsed, because historical keys collapse runs of
        spaces where a current normaliser preserves them, and an ampersand in
        a title was enough to cause a real miss;
      - the company is part of the key, so "same company, different role"
        produces a DIFFERENT key and can never collapse.
    """
    raw = f"{company or ''} :: {title or ''}".lower()
    return _WS.sub(" ", _PUNCT.sub(" ", raw)).strip()
