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

#: Sent by every outbound request this app makes, and the app does not work
#: without one. See `app/core/http.py` for the whole account — it belongs to
#: the client rather than to a provider, and putting it in a constant that
#: every call site had to remember is exactly how four more call sites came to
#: be found sending the urllib default on 2026-09-09.
#:
#: Re-exported here so the providers' imports keep working; the definition and
#: the reasoning live in one place.
from app.core.http import USER_AGENT  # noqa: F401


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
    #: Provider job ids we have ALREADY BEEN BILLED FOR, so the provider can
    #: leave them out. This is a billing control, not a tidiness one: the
    #: provider does not cache, so a row we already hold is re-bought every
    #: time it comes back.
    #:
    #: The delta pull above is the primary defence and this is the second. It
    #: earns its place on the day the delta mark FAILS TO ADVANCE — after a
    #: failed fetch, deliberately, so the run is not recorded as complete —
    #: because the next run then re-requests the same window and, without
    #: this, re-buys every row in it.
    exclude_job_ids: tuple[str, ...] = ()
    max_results: int = 500


@dataclass
class FetchResult:
    """Always carries the funnel numbers with the rows (spec 6.3)."""
    jobs: list[Job]
    pages_fetched: int = 0
    exhausted: bool = False          # spec 10.1: paginated to exhaustion?
    credits_estimate: int = 0
    error: str | None = None
    #: How many postings the query actually MATCHED upstream, when the provider
    #: reports it. `None` means unsized, which is not the same as zero.
    matched: int | None = None
    #: Matched but deliberately not fetched. Non-zero means the user is being
    #: shown less than exists, and something has to say so.
    not_fetched: int = 0
    #: True when the LICENCE CAP is the reason, as opposed to a page limit or a
    #: query that simply matched fewer rows. The distinction is what lets the
    #: app name the cause instead of reporting a vague shortfall.
    capped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def shortfall(self) -> str | None:
        """The sentence the user is owed when the run saw less than existed.

        spec 6.2: a truncated fetch is NEVER presented as "no new jobs", and a
        cap is never presented as a short list. Being capped is a fact about
        the user's plan, so it is named as one, with the numbers, rather than
        folded into a generic "results are partial".
        """
        if self.capped:
            total = self.matched if self.matched is not None else "?"
            return (f"capped: {total} postings matched today, "
                    f"{len(self.jobs)} fetched, {self.not_fetched} not fetched "
                    f"— the plan's daily limit was reached")
        if self.not_fetched:
            return (f"partial: {self.not_fetched} of {self.matched} matched "
                    f"postings were not fetched")
        if not self.exhausted:
            return "partial: pagination did not reach exhaustion"
        return None

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

    **The binding cap is the HOURLY one, not the per-minute headline.** Measured
    in P0: TheirStack's free tier is 4/second, 10/minute, 50/hour, 400/day, and
    a limiter that only enforces 6-second spacing satisfies 10/minute while
    sailing past 50/hour after eight minutes of steady work. Every window is
    enforced here, and `wait()` sleeps for whichever binds.
    """

    def __init__(self, per_second: int = 4, per_minute: int = 10,
                 per_hour: int = 50, per_day: int = 400):
        # (window seconds, max calls, timestamps)
        self._windows: list[tuple[float, int, list[float]]] = [
            (1.0, per_second, []),
            (60.0, per_minute, []),
            (3600.0, per_hour, []),
            (86400.0, per_day, []),
        ]

    def _sleep_needed(self, now: float) -> float:
        wait = 0.0
        for span, cap, hits in self._windows:
            if cap <= 0:
                continue
            hits[:] = [t for t in hits if now - t < span]
            if len(hits) >= cap:
                # The oldest call in this window has to age out first.
                wait = max(wait, span - (now - hits[0]))
        return wait

    def wait(self) -> None:
        while True:
            now = time.monotonic()
            needed = self._sleep_needed(now)
            if needed <= 0:
                break
            time.sleep(needed)
        now = time.monotonic()
        for _span, _cap, hits in self._windows:
            hits.append(now)

    def would_wait(self) -> float:
        """Seconds the next call would block for. Never sleeps — for planning
        a run and for telling the user why a sweep is slow."""
        return self._sleep_needed(time.monotonic())


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
