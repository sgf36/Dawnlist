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

    @property
    def ok(self) -> bool:
        return self.kind == COMPLETE


def _refreshes_stated(message: str | None) -> int:
    # "Daily refresh cap reached (3)" — the number is the licence's own.
    found = re.search(r"\((\d+)\)", message or "")
    return int(found.group(1)) if found else DEFAULT_REFRESHES_PER_DAY


def report_from_outcome(outcome) -> RunReport:
    fetched = list(outcome.fetch.values())
    capped = [r for r in fetched if r.refusal == REFRESH_CAP]
    if capped:
        # Named even when other searches got through. The rest of today's
        # searches did not run, and the fix — waiting for the UTC day — is
        # different from anything a partial fetch would suggest.
        return RunReport(LIMIT, detail=capped[0].error or "",
                         refreshes_per_day=_refreshes_stated(capped[0].error))
    if fetched and all(not r.ok for r in fetched):
        return RunReport(FETCH_FAILED, detail=outcome.fetch_errors[0])
    if not outcome.complete:
        detail = ""
        if outcome.fetch_errors:
            detail = outcome.fetch_errors[0]
        elif outcome.assessment and outcome.assessment.errors:
            detail = outcome.assessment.errors[0]
        return RunReport(PARTIAL, detail=detail)
    return RunReport(COMPLETE)


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
