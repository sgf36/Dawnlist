"""The managed feed: the app talks to Dawnlist's Worker, never to the provider.

WHY THIS EXISTS, WHEN `theirstack.py` ALREADY FETCHES JOBS
---------------------------------------------------------
`TheirStackProvider` calls `api.theirstack.com` directly with a TheirStack API
key. That is the bring-your-own-feed shape, and it was assessed and dropped:
the cheapest self-serve TheirStack subscription is about two and a half times
the managed price for half the allowance, so there is no user for whom it is
cheaper. It stays in the tree because it is what the coverage replays and
Spencer's own tooling run against — a developer instrument, not the product's
path.

The product's path is this one. The app holds a DAWNLIST licence key, not a
provider key, and the Worker holds the provider credential. That is what makes
three things possible at once:

  * metering and per-plan caps, enforced somewhere the user cannot edit;
  * one cached upstream call shared between users searching the same thing;
  * changing or adding a provider without shipping a new application.

WHAT IS DELIBERATELY NOT SENT
-----------------------------
Search terms and a licence key. Nothing else. No CV, no fit brief, no
factsheet, no job description ever travels to the Worker — the assessment runs
against Anthropic on the user's own key, directly from their machine. That is a
data-protection decision rather than a cost one, and an inference proxy that
would have broken it was built and removed on 2026-09-06. Do not route
assessment through here as a convenience.

BEING CAPPED IS A FACT, NOT AN ERROR
------------------------------------
When the plan's daily ceiling stops a run short, the Worker says so with the
numbers. This module turns that into a `FetchResult` that carries `capped`,
`matched` and `not_fetched` so the UI can say "your plan covered 700 of
today's 900" — spec 6.2 forbids presenting a truncated fetch as a complete one,
and an empty list with no explanation is the worst version of that.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.feed.base import (FeedProvider, FetchResult, SearchQuery, feed_job_ids,
                           parse_date)
from app.feed.models import Job

#: The deployed Worker. Overridable for local `wrangler dev` and for tests.
DEFAULT_BASE = "https://dawnlist-feed-worker.sgf36.workers.dev"

SEARCH_PATH = "/v1/search"
PLAN_PATH = "/v1/plan"

#: Identifies the client to our own service, and is REQUIRED rather than
#: courteous: Cloudflare blocks urllib's default agent with error 1010.
#:
#: Names the product and a contact, which is what a well-behaved client sends
#: and what makes an anomalous pattern in the Worker's logs traceable to a
#: version rather than to "some Python".
#: Moved to app/feed/base.py so both transports share one definition;
#: re-exported here because it was public from this module first.
from app.core.http import build_request  # noqa: E402
from app.feed.base import USER_AGENT  # noqa: E402,F401


@dataclass(frozen=True)
class PlanStatus:
    """What the licence is on, and what is left of it today.

    `plan` is None on a licence issued before plans existed, or one created by
    an override code. That is reported rather than guessed: showing "Standard"
    for a licence nobody assigned a plan to would be inventing a fact about
    somebody's subscription.
    """
    plan: str | None
    plan_assigned: bool
    tier: str
    postings_per_day: int
    postings_used: int
    postings_remaining: int
    refreshes_per_day: int
    refreshes_used: int
    #: Every plan the server knows, smallest ceiling first. The app does NOT
    #: keep its own copy: a client-side plan table is a second source of truth
    #: that goes stale the day a cap is revised.
    ladder: tuple[dict, ...] = ()

    @property
    def next_plan_up(self) -> dict | None:
        """The smallest plan that allows more than this one, if any.

        This is what an honest upgrade prompt needs: not "upgrade", but "the
        Global plan covers 2,500 a day". If the licence is already on the
        largest plan the answer is None and the UI must not offer an upgrade
        that does not exist.
        """
        bigger = [p for p in self.ladder
                  if p.get("postings_per_day", 0) > self.postings_per_day]
        return min(bigger, key=lambda p: p["postings_per_day"]) if bigger else None


class ManagedFeedError(RuntimeError):
    """A Worker refusal that the caller should show, not swallow."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ManagedProvider(FeedProvider):
    """Fetches through the Dawnlist Worker using a Dawnlist licence key."""

    name = "managed"

    def __init__(self, licence_key: str, *, base: str = DEFAULT_BASE,
                 timeout: int = 90):
        if not licence_key:
            raise ValueError("A Dawnlist licence key is required")
        self._licence = licence_key.strip()
        self._base = base.rstrip("/")
        self._timeout = timeout
        self._postings_used = 0

    # -- transport ---------------------------------------------------------
    def _call(self, path: str, body=None, method: str = "GET"):
        data = json.dumps(body).encode() if body is not None else None
        # Built through `build_request`, which sets the User-Agent for
        # every caller. See `app/core/http.py`: Cloudflare refuses
        # urllib's default agent with error 1010 and a 403, and a
        # constant each call site had to remember was forgotten in four
        # more places within two days of this one being fixed.
        req = build_request(self._base + path, data=data, method=method,
                            headers={"Authorization": f"Bearer {self._licence}",
                                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode()[:500]
            try:
                return e.code, json.loads(raw)
            except Exception:  # noqa: BLE001
                return e.code, {"error": "http_error", "message": raw}
        except Exception as e:  # noqa: BLE001
            return None, {"error": "unreachable", "message": repr(e)}

    # -- plan --------------------------------------------------------------
    def plan(self) -> PlanStatus:
        status, payload = self._call(PLAN_PATH)
        if status != 200:
            raise ManagedFeedError(payload.get("error", "plan_failed"),
                                   payload.get("message", "Could not read the plan"))
        caps = payload.get("caps", {})
        used = payload.get("used_today", {})
        left = payload.get("remaining_today", {})
        return PlanStatus(
            plan=payload.get("plan"),
            plan_assigned=bool(payload.get("plan_assigned")),
            tier=payload.get("tier", ""),
            postings_per_day=int(caps.get("postings_per_day", 0)),
            postings_used=int(used.get("postings", 0)),
            postings_remaining=int(left.get("postings", 0)),
            refreshes_per_day=int(caps.get("refreshes_per_day", 0)),
            refreshes_used=int(used.get("refreshes", 0)),
            ladder=tuple(payload.get("plans", ())),
        )

    # -- search ------------------------------------------------------------
    def search(self, query: SearchQuery) -> FetchResult:
        body = {
            "label": query.label,
            "titles": list(query.titles),
            "countries": list(query.countries),
            "companies": list(query.companies),
            "postedWithinDays": query.posted_within_days,
            "discoveredSince": (query.discovered_since.isoformat()
                                if query.discovered_since else None),
            "excludeJobIds": feed_job_ids(query.exclude_job_ids),
            "maxResults": query.max_results,
            "cities": list(query.cities),
            "excludeTitleTerms": list(query.exclude_title_terms),
            "excludeCompanies": list(query.exclude_companies),
        }
        status, payload = self._call(SEARCH_PATH, body, method="POST")

        if status == 429:
            # The cap. NOT an error state to hide: the run continues with what
            # was already gathered, and the user is told in plain numbers.
            code = payload.get("error", "cap")
            return FetchResult(jobs=[], capped=(code == "posting_cap"),
                               error=payload.get("message", "Daily limit reached"),
                               refusal=code)
        if status != 200:
            return FetchResult(jobs=[], error=_message(status, payload))

        counts = payload.get("counts", {}) or {}
        jobs = [_job(row) for row in payload.get("jobs", [])]
        self._postings_used = int(counts.get("postings_used", 0))

        return FetchResult(
            jobs=jobs,
            pages_fetched=1,
            # The Worker paginates upstream; one call to it is the whole query.
            exhausted=not counts.get("not_fetched"),
            credits_estimate=len(jobs),
            matched=counts.get("matched"),
            not_fetched=int(counts.get("not_fetched", 0) or 0),
            capped=bool(counts.get("capped")),
        )

    def credits_used(self) -> int | None:
        """Postings drawn against today's allowance, as the server counts them.

        Deliberately the SERVER's number rather than a local tally: a shared
        cache hit still spends the user's allowance, and only the Worker knows
        what it delivered.
        """
        return self._postings_used


def _message(status, payload) -> str:
    code = payload.get("error", "error")
    detail = payload.get("message", "")
    if code == "unknown_licence":
        return "This licence key was not recognised."
    if code == "licence_inactive":
        return f"This licence is not active — {detail}"
    if code == "provider_error":
        return f"The job feed is unavailable right now ({detail})."
    if code == "unreachable":
        return "Could not reach the Dawnlist feed service."
    return f"{code}: {detail}" if detail else str(code)


def _job(row: dict) -> Job:
    """The Worker already normalises, so this is a mapping and not a parser.

    Kept explicit rather than `Job(**row)` so an added server field cannot
    raise a TypeError in a released application.
    """
    return Job(
        provider=row.get("provider", "managed"),
        provider_job_id=str(row.get("provider_job_id", "")),
        title=row.get("title", "") or "",
        company=row.get("company", "") or "",
        locations=tuple(row.get("locations", ()) or ()),
        description_text=row.get("description_text", "") or "",
        posted_at=parse_date(row.get("posted_at")),
        salary=row.get("salary"),
        url=row.get("url", "") or "",
        raw_criteria=row.get("raw_criteria", {}) or {},
    )
