"""LinkedIn Job Library API adapter.

The Job Library is a sub-API of the LinkedIn Ad Library product. It returns
only PAID/SPONSORED job posts — the subset employers paid to promote. This is
complementary to the URL import (which handles any public listing) and the
boolean query generator (which helps users search LinkedIn's own UI).

Auth: 3-legged OAuth via the Ad Library product. The token is a member token
generated through Sign In with LinkedIn (openid scope), stored in keyring.
The Ad Library product grants access at the app level — no dedicated scope.

API docs: https://www.linkedin.com/ad-library/api/jobs
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

from app.core.http import build_request
from app.feed.base import (FeedProvider, FetchResult, SearchQuery, parse_date)
from app.feed.models import Job

PROVIDER_NAME = "linkedin-joblibrary"
BASE = "https://api.linkedin.com/rest/jobLibrary"
PAGE_SIZE = 24

_COUNTRY_URNS = {
    "gb": "urn:li:country:gb",
    "uk": "urn:li:country:gb",
    "us": "urn:li:country:us",
    "de": "urn:li:country:de",
    "fr": "urn:li:country:fr",
    "nl": "urn:li:country:nl",
    "ie": "urn:li:country:ie",
    "ch": "urn:li:country:ch",
    "ae": "urn:li:country:ae",
    "sg": "urn:li:country:sg",
    "au": "urn:li:country:au",
    "ca": "urn:li:country:ca",
    "es": "urn:li:country:es",
    "it": "urn:li:country:it",
    "at": "urn:li:country:at",
    "se": "urn:li:country:se",
    "dk": "urn:li:country:dk",
    "no": "urn:li:country:no",
    "be": "urn:li:country:be",
    "pt": "urn:li:country:pt",
    "in": "urn:li:country:in",
    "jp": "urn:li:country:jp",
    "hk": "urn:li:country:hk",
}


def _country_urn(code: str) -> str:
    low = code.strip().lower()
    return _COUNTRY_URNS.get(low, f"urn:li:country:{low}")


def _build_params(q: SearchQuery, start: int, count: int) -> dict[str, str]:
    params: dict[str, str] = {
        "q": "criteria",
        "start": str(start),
        "count": str(count),
        "sortBy.field": "LISTED_TIME",
        "sortBy.order": "DESCENDING",
    }
    keywords = list(q.titles)
    if q.description_keywords:
        keywords.extend(q.description_keywords)
    if keywords:
        params["keyword"] = " ".join(keywords)
    if q.companies:
        params["organization"] = q.companies[0]
    if q.countries:
        urns = [_country_urn(c) for c in q.countries]
        urn_list = ",".join(urns)
        params["countries"] = f"(value:List({urn_list}))"
    return params


def _parse_salary(sal: dict | None) -> str | None:
    if not sal:
        return None
    lo = sal.get("minBaseSalary")
    hi = sal.get("maxBaseSalary")
    cur = sal.get("currencyCode", "")
    period = sal.get("payPeriod", "")
    period_label = ""
    if period:
        period = period.replace("CompensationPeriod_", "")
        if period not in ("", "YEARLY"):
            period_label = f" ({period.lower()})"
    if lo and hi:
        return f"{cur} {lo}–{hi}{period_label}".strip()
    if lo:
        return f"{cur} {lo}+{period_label}".strip()
    if hi:
        return f"Up to {cur} {hi}{period_label}".strip()
    return None


def _to_job(element: dict) -> Job | None:
    details = element.get("jobDetails")
    if not details:
        return None
    title = details.get("jobTitle", "")
    if title.startswith("This information is not available"):
        return None
    company = details.get("organizationName", "")
    location = details.get("jobLocation", "")
    locations = (location,) if location and not location.startswith("This information") else ()
    description = details.get("jobDescription", "")
    if description.startswith("This information is not available"):
        description = ""
    posted_ms = details.get("jobListTimeInMilliseconds")
    posted_at = None
    if posted_ms:
        posted_at = datetime.fromtimestamp(
            posted_ms / 1000, tz=timezone.utc).date()
    url = element.get("jobPostingUrl", "")
    job_id = ""
    if url:
        parts = url.rstrip("/").rsplit("/", 1)
        if len(parts) == 2 and parts[1].isdigit():
            job_id = parts[1]
    if not job_id:
        job_id = url
    raw: dict = {}
    targeting = details.get("jobTargeting") or []
    for t in targeting:
        if t.get("facetName") == "Location" and t.get("includedSegments"):
            raw["target_locations"] = t["includedSegments"]
    stats = details.get("jobStatistics") or {}
    impressions = stats.get("totalImpressions")
    if impressions:
        raw["impressions"] = impressions
    apply_method = details.get("jobApplyMethod")
    if apply_method:
        raw["apply_method"] = apply_method
    payer = details.get("payerName")
    if payer:
        raw["payer"] = payer
    benefits = details.get("jobBenefits")
    if benefits:
        raw["benefits"] = benefits

    return Job(
        provider=PROVIDER_NAME,
        provider_job_id=job_id,
        title=title,
        company=company,
        locations=locations,
        description_text=description,
        posted_at=posted_at,
        salary=_parse_salary(details.get("jobSalaryRange")),
        url=url,
        raw_criteria=raw,
    )


class LinkedInJobLibraryProvider(FeedProvider):
    name = PROVIDER_NAME

    def __init__(self, token: str, *, timeout: int = 30):
        if not token:
            raise ValueError("LinkedIn OAuth token is required")
        self._token = token
        self._timeout = timeout

    def _call(self, params: dict[str, str]) -> tuple[int | None, dict | str]:
        req = build_request(BASE, headers={
            "Authorization": f"Bearer {self._token}",
            "X-RestLi-Protocol-Version": "2.0.0",
            "Linkedin-Version": "202607",
        })
        qs = urllib.request.quote(
            "&".join(f"{k}={v}" for k, v in params.items()),
            safe="&=():,")
        req.full_url = f"{BASE}?{qs}"

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:500]
            except Exception:
                pass
            return e.code, body
        except Exception as e:
            return None, repr(e)

    def search(self, query: SearchQuery) -> FetchResult:
        jobs: list[Job] = []
        max_results = min(query.max_results, 240)
        pages = 0
        exhausted = False
        matched: int | None = None

        while len(jobs) < max_results:
            remaining = max_results - len(jobs)
            count = min(PAGE_SIZE, remaining)
            start = pages * PAGE_SIZE

            status, payload = self._call(
                _build_params(query, start, count))

            if status == 401:
                return FetchResult(
                    jobs=jobs, pages_fetched=pages,
                    error="LinkedIn token expired or invalid — "
                          "regenerate at developers.linkedin.com",
                    refusal="token_expired")

            if status != 200 or not isinstance(payload, dict):
                return FetchResult(
                    jobs=jobs, pages_fetched=pages,
                    error=f"LinkedIn jobLibrary returned "
                          f"{status}: {str(payload)[:200]}")

            paging = payload.get("paging") or {}
            if matched is None:
                matched = paging.get("total")

            elements = payload.get("elements") or []
            for el in elements:
                if el.get("isRestricted"):
                    continue
                job = _to_job(el)
                if job:
                    jobs.append(job)

            pages += 1
            if len(elements) < count:
                exhausted = True
                break
            if not paging.get("links"):
                exhausted = True
                break

        return FetchResult(
            jobs=jobs,
            pages_fetched=pages,
            exhausted=exhausted,
            matched=matched,
        )

    def credits_used(self) -> int | None:
        return None
