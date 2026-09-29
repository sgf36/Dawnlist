"""Import jobs from LinkedIn URLs via public JSON-LD structured data.

LinkedIn embeds a `JobPosting` JSON-LD block in every public job page for
search engines and social media previews.  Reading it is functionally identical
to what Slack, iMessage or Google does when someone pastes a LinkedIn URL.

This does NOT scrape: it reads one page per URL the user explicitly provided,
extracts only the structured data LinkedIn publishes for machines, and makes no
authenticated requests.  A daily cap prevents the feature being repurposed as
a bulk scraper.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from app.core.http import build_request
from app.feed.base import parse_date
from app.feed.models import Job

PROVIDER = "linkedin-import"

#: A person finding jobs by hand will not paste more than this in a day.
#: Anything higher is automation wearing a human mask.
DAILY_IMPORT_CAP = 25

_JOB_URL = re.compile(
    r"https?://(?:www\.)?linkedin\.com/(?:jobs/view|comm/jobs/view)/(\d+)",
    re.IGNORECASE)

_JSON_LD = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ImportResult:
    job: Job | None = None
    url: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.job is not None and self.error is None


def extract_job_id(url: str) -> str | None:
    """The numeric LinkedIn job ID from a URL, or None."""
    m = _JOB_URL.search(url.strip())
    return m.group(1) if m else None


def _find_job_posting(html_text: str) -> dict | None:
    """The first JobPosting JSON-LD block from the page HTML."""
    for m in _JSON_LD.finditer(html_text):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    return item
        elif isinstance(data, dict):
            if data.get("@type") == "JobPosting":
                return data
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        return item
    return None


def _extract_location(posting: dict) -> str:
    locs: list[str] = []
    jl = posting.get("jobLocation")
    if isinstance(jl, dict):
        jl = [jl]
    if isinstance(jl, list):
        for loc in jl:
            if not isinstance(loc, dict):
                continue
            addr = loc.get("address")
            if isinstance(addr, dict):
                parts = [addr.get("addressLocality", ""),
                         addr.get("addressRegion", ""),
                         addr.get("addressCountry", "")]
                locs.append(", ".join(p.strip() for p in parts if p.strip()))
            elif isinstance(loc.get("name"), str):
                locs.append(loc["name"])
    return "; ".join(locs) if locs else ""


def _extract_salary(posting: dict) -> str | None:
    bp = posting.get("baseSalary") or posting.get("estimatedSalary")
    if not isinstance(bp, dict):
        return None
    value = bp.get("value")
    currency = bp.get("currency", "")
    if isinstance(value, dict):
        lo = value.get("minValue", "")
        hi = value.get("maxValue", "")
        unit = value.get("unitText", "")
        if lo and hi:
            return f"{currency} {lo}–{hi} {unit}".strip()
        elif lo:
            return f"{currency} {lo}+ {unit}".strip()
    elif value:
        return f"{currency} {value}".strip()
    return None


def _clean_description(text: str) -> str:
    """Strip HTML tags from a description, keeping whitespace structure."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|ul|ol|li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _to_job(posting: dict, url: str, job_id: str) -> Job:
    title = posting.get("title", "")
    company = ""
    org = posting.get("hiringOrganization")
    if isinstance(org, dict):
        company = org.get("name", "")
    elif isinstance(org, str):
        company = org

    description = _clean_description(posting.get("description", ""))
    location = _extract_location(posting)
    salary = _extract_salary(posting)
    posted = parse_date(posting.get("datePosted"))

    return Job(
        provider=PROVIDER,
        provider_job_id=job_id,
        title=title,
        company=company,
        locations=(location,) if location else (),
        description_text=description,
        posted_at=posted,
        salary=salary,
        url=url,
        raw_criteria={"source": "linkedin-import"},
    )


class DailyCapExceeded(RuntimeError):
    """The user has hit the daily import cap."""

    def __init__(self, used: int, cap: int):
        self.used = used
        self.cap = cap
        super().__init__(f"{used}/{cap} imports used today")


class _DailyCounter:
    """Tracks how many LinkedIn pages have been fetched today.

    Stored in a single-row table so it resets naturally at midnight and
    survives an app restart.  The cap is enforced HERE, not in the UI —
    every path through `fetch_job` hits this, so no caller can bypass it.
    """

    def __init__(self, conn):
        self._conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS linkedin_import_cap "
            "(day TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0)")

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def used_today(self) -> int:
        row = self._conn.execute(
            "SELECT used FROM linkedin_import_cap WHERE day = ?",
            (self._today(),)).fetchone()
        return row["used"] if row else 0

    def remaining(self) -> int:
        return max(0, DAILY_IMPORT_CAP - self.used_today())

    def increment(self) -> None:
        day = self._today()
        self._conn.execute(
            "INSERT INTO linkedin_import_cap (day, used) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET used = used + 1",
            (day,))
        self._conn.commit()

    def check(self, count: int = 1) -> None:
        """Raise if importing `count` more jobs would exceed the cap."""
        used = self.used_today()
        if used + count > DAILY_IMPORT_CAP:
            raise DailyCapExceeded(used, DAILY_IMPORT_CAP)


def fetch_job(url: str, *, conn=None, timeout: int = 15) -> ImportResult:
    """Fetch a single LinkedIn job page and extract the posting.

    When `conn` is provided the daily cap is enforced. Without it the cap
    is not checked — only tests omit it.
    """
    url = url.strip()
    job_id = extract_job_id(url)
    if not job_id:
        return ImportResult(url=url, error="not a LinkedIn job URL")

    if conn is not None:
        counter = _DailyCounter(conn)
        counter.check()

    canonical = f"https://www.linkedin.com/jobs/view/{job_id}"

    try:
        req = build_request(canonical)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html_text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ImportResult(url=url, error="job not found (404)")
        if e.code == 429:
            return ImportResult(url=url, error="rate limited — try again shortly")
        return ImportResult(url=url, error=f"HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        return ImportResult(url=url, error=str(e) or type(e).__name__)

    posting = _find_job_posting(html_text)
    if posting is None:
        return ImportResult(url=url,
                            error="no structured job data found on this page")

    job = _to_job(posting, canonical, job_id)
    if not job.title:
        return ImportResult(url=url, error="page had no job title")

    if conn is not None:
        counter.increment()

    return ImportResult(job=job, url=canonical)


def fetch_many(urls: list[str], *, conn=None,
               timeout: int = 15) -> list[ImportResult]:
    """Fetch multiple LinkedIn job URLs. No parallelism — polite single-thread.

    The daily cap is checked upfront for the whole batch, so a user
    pasting 30 URLs when they have 5 remaining gets a clear refusal
    rather than 5 successes and 25 cap errors.
    """
    import time

    if conn is not None:
        counter = _DailyCounter(conn)
        counter.check(len(urls))

    results: list[ImportResult] = []
    for i, url in enumerate(urls):
        if i > 0:
            time.sleep(1.0)
        results.append(fetch_job(url, conn=conn, timeout=timeout))
    return results


def parse_url_list(text: str) -> list[str]:
    """Extract LinkedIn job URLs from pasted text (one per line, or mixed)."""
    urls: list[str] = []
    for m in _JOB_URL.finditer(text):
        canonical = f"https://www.linkedin.com/jobs/view/{m.group(1)}"
        if canonical not in urls:
            urls.append(canonical)
    return urls
