"""TheirStack adapter.

Every behaviour encoded here was MEASURED against the live API during P0
(dawnlist-p0/ts_probe.py, ts_calibrate.py, ts_replay.py), not read off a
pricing page:

  * 1 API credit = 1 job RETURNED. Confirmed: one search with limit=1 moved the
    counter from 0 to 1. This is why `max_results` is a hard cost ceiling and
    why delta pulls are mandatory rather than an optimisation.
  * A miss costs nothing. A query matching no jobs consumed 0 credits, verified
    with a nonsense company name. That is what makes a narrow probe affordable.
  * `job_title_or` matches LOOSELY — "Product Manager" returned "Senior Product
    Manager, EG Advertising Marketplace". Substring, not exact.
  * Free tier: 4/second, 10/minute, 50/hour, 400/day. The per-minute cap binds.
  * Descriptions are full text (7,426 chars on the probe) and URLs are
    ATS-canonical (a Workday link for Expedia, not a LinkedIn mirror) — the
    description feeds the assessment prompt and the canonical URL is what the
    user should apply through.

P0 coverage result: cohort A 23/23 (the gate), cohort B 18/22 (indicative).
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app.feed.base import (FeedProvider, FetchResult, RateLimiter, SearchQuery,
                           parse_date)
from app.feed.models import Job, name_key

BASE = "https://api.theirstack.com"
SEARCH_PATH = "/v1/jobs/search"
CREDITS_PATH = "/v0/teams/credits_consumption"
PAGE_SIZE = 100

# The Windows console is cp1252 and job titles are not. The first P0 run died
# on an en-dash AFTER the credits had already been spent.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


class TheirStackProvider(FeedProvider):
    name = "theirstack"

    def __init__(self, api_key: str, *, per_minute: int = 10, timeout: int = 60):
        if not api_key:
            raise ValueError("TheirStack API key is required")
        self._key = api_key
        self._limiter = RateLimiter(per_minute=per_minute)
        self._timeout = timeout

    # -- transport ---------------------------------------------------------
    def _call(self, path: str, body=None, method: str = "GET"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._key}")
        req.add_header("Content-Type", "application/json")

        for attempt in range(3):
            self._limiter.wait()
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # Read retryAfter and sleep it out; an instant retry just
                    # re-hits the limit. Observed 10-37s in production.
                    retry_after = e.headers.get("Retry-After")
                    delay = int(retry_after) if (retry_after or "").isdigit() else 20 * (attempt + 1)
                    import time
                    time.sleep(min(delay, 60))
                    continue
                if e.code in (502, 503, 504):
                    continue          # a gateway timeout wants an immediate retry
                return e.code, e.read().decode()[:400]
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    return None, repr(e)
        return None, "retries exhausted"

    # -- search ------------------------------------------------------------
    def _body(self, q: SearchQuery, page: int, limit: int) -> dict:
        body: dict = {
            "limit": limit,
            "page": page,
            "include_total_results": False,
        }
        if q.titles:
            body["job_title_or"] = q.titles
        if q.countries:
            body["job_country_code_or"] = q.countries
        if q.companies:
            body["company_name_or"] = q.companies
        if q.posted_within_days:
            body["posted_at_max_age_days"] = q.posted_within_days
        if q.discovered_since:
            # The delta pull. Only postings first indexed since the last run.
            body["discovered_at_gte"] = q.discovered_since.astimezone(
                timezone.utc).strftime("%Y-%m-%d")
        return body

    def search(self, query: SearchQuery) -> FetchResult:
        jobs: list[Job] = []
        pages = 0
        exhausted = False

        while len(jobs) < query.max_results:
            remaining = query.max_results - len(jobs)
            limit = min(PAGE_SIZE, remaining)
            status, payload = self._call(
                SEARCH_PATH, self._body(query, pages, limit), "POST")

            if status != 200 or not isinstance(payload, dict):
                # spec 6.2: surface it. A partial page already fetched is
                # returned WITH the error so the caller can report both.
                return FetchResult(jobs=jobs, pages_fetched=pages,
                                   exhausted=False, credits_estimate=len(jobs),
                                   error=f"TheirStack {SEARCH_PATH} returned "
                                         f"{status}: {str(payload)[:200]}")

            rows = payload.get("data") or []
            jobs.extend(self._to_job(r) for r in rows)
            pages += 1

            if len(rows) < limit:
                exhausted = True      # spec 10.1: paginated to exhaustion
                break

        return FetchResult(jobs=jobs, pages_fetched=pages, exhausted=exhausted,
                           credits_estimate=len(jobs))

    def _to_job(self, row: dict) -> Job:
        company = (row.get("company")
                   or (row.get("company_object") or {}).get("name") or "")
        title = row.get("job_title") or ""
        locations = [x for x in (row.get("location"),
                                 row.get("short_location"),
                                 row.get("long_location")) if x]
        return Job(
            provider=self.name,
            provider_job_id=str(row.get("id") or row.get("job_id") or row.get("url") or ""),
            title=title,
            company=company,
            locations=tuple(dict.fromkeys(locations)),
            description_text=row.get("description") or "",
            posted_at=parse_date(row.get("date_posted")),
            salary=row.get("salary_string") or None,
            # ATS-canonical where the provider gives one: that is the link the
            # user should actually apply through.
            url=row.get("final_url") or row.get("url") or row.get("source_url") or "",
            raw_criteria={k: row.get(k) for k in
                          ("seniority", "industry", "remote", "employment_statuses")
                          if row.get(k) is not None},
        )

    # -- metering ----------------------------------------------------------
    def credits_used(self) -> int | None:
        status, payload = self._call(CREDITS_PATH)
        if status == 200 and isinstance(payload, list):
            return sum(d.get("api_credits_consumed", 0) for d in payload)
        return None
