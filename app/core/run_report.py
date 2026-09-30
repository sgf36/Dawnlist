"""What happened to a run, as plain data that can cross a thread.

A run started from the window runs on a worker thread and its answer is shown
on the UI thread. The outcome object holds every fetched posting, and the
exceptions it can raise carry sentences written for a terminal, so neither is
what the window should be reading. This reduces both to one small value the
window turns into a translated sentence.

The categories are the ones the user can do something different about: set
something up, pay, fix a key, wait for the feed's day to roll over, or try
again. Folding them together is how "nothing happened" ends up as the only
message anyone sees.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

COMPLETE = "complete"
PARTIAL = "partial"
#: The Worker's per-UTC-day refresh allowance is used up.
LIMIT = "limit"
FETCH_FAILED = "fetch_failed"
NOT_CONFIGURED = "not_configured"
NOT_ENTITLED = "not_entitled"
KEY = "key"
ERROR = "error"

#: The Worker's fallback allowance (`DEFAULTS.maxRefreshesPerDay` in its
#: index.js), used only when its refusal does not state the number. Every sold
#: plan states it; this keeps the sentence true for a licence that does not.
DEFAULT_REFRESHES_PER_DAY = 3

REFRESH_CAP = "refresh_cap"


@dataclass(frozen=True)
class RunReport:
    kind: str
    detail: str = ""
    refreshes_per_day: int | None = None
    #: What the run actually did, so a run that "completed" without reading
    #: anything can say so. Zero everywhere means the figures were not
    #: measured (an error before a run existed), not that nothing happened.
    swept: int = 0
    searches: int = 0
    empty_searches: int = 0
    assessed: int = 0
    seconds: float = 0.0
    #: Display names of providers that could not be searched this run although
    #: others answered. The detail (an expired token, an outage) is for the
    #: diagnostics log: a customer cannot act on it, and can act on "some
    #: sponsored listings are missing".
    degraded: tuple = ()

    @property
    def thin(self) -> bool:
        """A run that finished cleanly but fetched nothing, or came back empty
        from at least half of its searches. Not an error — a quiet market and a
        search too narrow to find anything look identical from here — but never
        something to file under "up to date"."""
        if self.kind != COMPLETE or not self.searches:
            return False
        return self.swept == 0 or self.empty_searches * 2 >= self.searches

    @property
    def ok(self) -> bool:
        return self.kind == COMPLETE


def _refreshes_stated(message: str | None) -> int:
    # "Daily refresh cap reached (3)" — the number is the licence's own.
    found = re.search(r"\((\d+)\)", message or "")
    return int(found.group(1)) if found else DEFAULT_REFRESHES_PER_DAY


#: What each provider is called to the person reading the banner.
PROVIDER_LABELS = {"linkedin": "LinkedIn Jobs", "theirstack": "the main job feed"}


def _measured(outcome) -> dict:
    fetched = list(outcome.fetch.values())
    return dict(
        degraded=tuple(PROVIDER_LABELS.get(d["provider"], d["provider"])
                       for d in getattr(outcome, "provider_degraded", [])),
        swept=sum(len(r.jobs) for r in fetched),
        searches=len(fetched),
        empty_searches=sum(1 for r in fetched if r.ok and not r.jobs),
        assessed=len(outcome.assessment.verdicts) if outcome.assessment else 0,
        seconds=float(getattr(outcome, "elapsed_seconds", 0.0) or 0.0))


def report_from_outcome(outcome) -> RunReport:
    fetched = list(outcome.fetch.values())
    facts = _measured(outcome)
    capped = [r for r in fetched if r.refusal == REFRESH_CAP]
    if capped:
        # Named even when other searches got through. The rest of today's
        # searches did not run, and the fix — waiting for the UTC day — is
        # different from anything a partial fetch would suggest.
        return RunReport(LIMIT, detail=capped[0].error or "",
                         refreshes_per_day=_refreshes_stated(capped[0].error),
                         **facts)
    if fetched and all(not r.ok for r in fetched):
        return RunReport(FETCH_FAILED, detail=outcome.fetch_errors[0], **facts)
    if not outcome.complete:
        detail = ""
        if outcome.fetch_errors:
            detail = outcome.fetch_errors[0]
        elif outcome.assessment and outcome.assessment.errors:
            detail = outcome.assessment.errors[0]
        return RunReport(PARTIAL, detail=detail, **facts)
    return RunReport(COMPLETE, **facts)


def report_from_error(exc: BaseException) -> RunReport:
    from app.core.api_key import KeyProblem
    from app.core.entitlement import NotEntitled
    from app.main import NotConfigured

    if isinstance(exc, KeyProblem):
        return RunReport(KEY, detail=str(exc))
    if isinstance(exc, NotEntitled):
        return RunReport(NOT_ENTITLED, detail=str(exc))
    if isinstance(exc, NotConfigured):
        return RunReport(NOT_CONFIGURED, detail=str(exc))
    return RunReport(ERROR, detail=f"{type(exc).__name__}: {exc}"[:500])
