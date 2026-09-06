"""Provider adapter contract.

The app never speaks a provider's API shape — only this one. That is what makes
swapping or adding a feed provider a server-side config change with no app
release (handoff Part 0.3), and what lets a TheirStack *dataset* index later
become just another adapter behind the same normalised Job (Part 2.1b).
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.feed.models import Job


class FeedError(RuntimeError):
    """A fetch that failed. NEVER swallowed into 'no new jobs' (spec 6.2)."""


@dataclass
class SearchQuery:
    label: str
    titles: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    posted_within_days: int | None = None
    # handoff 2.1a: the delta pull. Billing is per job RETURNED, so re-fetching
    # yesterday's postings is re-buying them. A daily delta is ~100 postings,
    # not ~1,000 — the difference between $3.30 and $33 per user per month at
    # the top tier, and between $98 and $981 at the bottom one.
    discovered_since: datetime | None = None
    max_results: int = 500


@dataclass
class FetchResult:
    """Always carries the funnel numbers with the rows (spec 6.3)."""
    jobs: list[Job]
    pages_fetched: int = 0
    exhausted: bool = False          # spec 10.1: paginated to exhaustion?
    credits_estimate: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def raise_if_failed(self) -> "FetchResult":
        if self.error:
            raise FeedError(self.error)
        return self


class FeedProvider(ABC):
    name: str

    @abstractmethod
    def search(self, query: SearchQuery) -> FetchResult: ...

    @abstractmethod
    def credits_used(self) -> int | None: ...


class RateLimiter:
    """spec 10.1: a rate-limit error and a gateway timeout need OPPOSITE
    remedies. Retry a 504 immediately; for a 429 an instant retry just re-hits
    the limit, so read retryAfter, sleep it out, then retry once.
    """

    def __init__(self, per_minute: int = 10, per_second: int = 4):
        self.min_interval = max(60.0 / per_minute, 1.0 / per_second)
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


def parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat,):
        try:
            return parse(text).date()
        except ValueError:
            pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
