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
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from app.core import places
from app.core.http import build_request
from app.feed.base import (FeedProvider, FetchResult, SearchQuery, parse_date)
from app.feed.models import Job

PROVIDER_NAME = "linkedin-joblibrary"
BASE = "https://api.linkedin.com/rest/jobLibrary"
PAGE_SIZE = 24
#: Raw pages scanned per search, however few postings survive the filters.
#: The API is relevance-ranked over descriptions with no city, title or date
#: filter, so a search like "Hotel Manager" matches ~45,000 UK postings of
#: which a few dozen are hotel-manager jobs in London. Reading them all is
#: ~1,900 requests; this bounds the cost (~0.7 s a page) and `exhausted` stays
#: False when the budget, not the data, ended the scan — a truncated scan is
#: never presented as "nothing more".
PAGE_BUDGET = 30

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
        # Rest.li list syntax. The colons inside each URN are percent-encoded
        # by `_encode_query`; the older `(value:List(...))` form 400s live.
        params["countries"] = f"List({urn_list})"
    return params


def _encode_query(params: dict[str, str]) -> str:
    """Encode each VALUE separately, keeping Rest.li's ``List(a,b)`` structure.

    Encoding the joined string in one go left the colons of a URN raw, which
    the API refuses (400 "Invalid query parameters") — and quoting twice would
    mangle a keyword's own punctuation.
    """
    return "&".join(
        f"{k}={urllib.request.quote(v, safe='(),')}" for k, v in params.items())


_SUFFIXES = ("ement", "ers", "er", "ing", "ment", "ant", "ancy", "s")


def _stem(word: str) -> str:
    """Crude enough to match 'Manager' to 'Management' and nothing looser."""
    w = word.lower()
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[A-Za-z0-9]+", text)}


def _title_matches(job_title: str, wanted_titles: list[str]) -> bool:
    """Every word of at least one wanted title appears in the posting's title,
    in any order — the same word-set reading the TheirStack feed applies.

    The API matches the keyword against the whole description, so without this
    a search for "Hotel Manager" returned a supermarket assistant.
    """
    have = _words(job_title)
    return any(_words(t) <= have for t in wanted_titles if _words(t))


def _city_verdict(locations: tuple[str, ...], cities: list[str]):
    """True / False / None (place not stated) — see `places.location_matches`.

    The API offers no city filter, only country, and the names it returns for
    Greater London are borough and district names ("Croydon", "Canary Wharf",
    "HA3 0AA, Harrow"), so a test for the word "London" silently dropped them.
    """
    return places.location_matches(locations, cities)


def _keep(job: Job, q: SearchQuery, today: date) -> bool:
    if q.search_type in (None, "title") and q.titles:
        if not _title_matches(job.title, q.titles):
            return False
    if q.cities:
        verdict = _city_verdict(job.locations, q.cities)
        if verdict is False:
            return False
        if verdict is None:
            # Located only as "United Kingdom" / "England": unknown is not
            # evidence of elsewhere, so it is kept, and marked so the
            # assessment (which reads the description) knows the place is open.
            job.raw_criteria["location_unresolved"] = True
    if q.posted_within_days and job.posted_at is not None:
        if job.posted_at < today - timedelta(days=q.posted_within_days):
            return False
    for term in q.exclude_title_terms:
        if term.strip() and re.search(
                r"\b" + re.escape(term.strip()) + r"\b", job.title, re.I):
            return False
    return True


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
        req.full_url = f"{BASE}?{_encode_query(params)}"

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
        if not any(k in _build_params(query, 0, 1)
                   for k in ("keyword", "organization")):
            # The API answers a criteria search with no keyword or
            # organisation with a bare 500, which reads as an outage.
            return FetchResult(
                jobs=[], pages_fetched=0,
                error="LinkedIn jobLibrary needs a keyword or organisation")

        jobs: list[Job] = []
        wanted = min(query.max_results, 240)
        today = datetime.now(timezone.utc).date()
        country_codes = [c.upper() for c in query.countries]
        pages = 0
        scanned = 0
        exhausted = False
        matched: int | None = None

        while len(jobs) < wanted and pages < PAGE_BUDGET:
            status, payload = self._call(
                _build_params(query, pages * PAGE_SIZE, PAGE_SIZE))

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
                if job is None:
                    continue
                scanned += 1
                if country_codes:
                    # The request was country-scoped, so say so: the run's
                    # location gate only reads this tag and used to pass every
                    # LinkedIn posting because none carried one.
                    job.raw_criteria["country_codes"] = country_codes
                if _keep(job, query, today):
                    jobs.append(job)
                    if len(jobs) >= wanted:
                        break

            pages += 1
            # A page can come back a row short (23 of 24) with more still
            # behind it, so a short page is NOT the end: the `next` link is.
            if not elements or not paging.get("links"):
                exhausted = True
                break

        return FetchResult(
            jobs=jobs, pages_fetched=pages, exhausted=exhausted,
            matched=matched, scanned=scanned)

    def credits_used(self) -> int | None:
        return None
