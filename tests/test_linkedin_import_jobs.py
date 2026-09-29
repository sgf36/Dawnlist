"""Tests for the LinkedIn URL import module."""
from __future__ import annotations

import json
import sqlite3
import textwrap

import pytest

from app.linkedin.import_jobs import (
    DAILY_IMPORT_CAP,
    DailyCapExceeded,
    ImportResult,
    _DailyCounter,
    _clean_description,
    _extract_location,
    _extract_salary,
    _find_job_posting,
    _to_job,
    extract_job_id,
    parse_url_list,
)


# -- extract_job_id -----------------------------------------------------------

class TestExtractJobId:
    def test_standard_url(self):
        assert extract_job_id(
            "https://www.linkedin.com/jobs/view/1234567890") == "1234567890"

    def test_no_www(self):
        assert extract_job_id(
            "https://linkedin.com/jobs/view/42") == "42"

    def test_comm_variant(self):
        assert extract_job_id(
            "https://www.linkedin.com/comm/jobs/view/99") == "99"

    def test_trailing_params(self):
        assert extract_job_id(
            "https://www.linkedin.com/jobs/view/123?trk=abc") == "123"

    def test_not_a_job_url(self):
        assert extract_job_id("https://www.linkedin.com/in/someone") is None

    def test_empty(self):
        assert extract_job_id("") is None


# -- parse_url_list ------------------------------------------------------------

class TestParseUrlList:
    def test_single(self):
        assert parse_url_list(
            "https://www.linkedin.com/jobs/view/111") == [
            "https://www.linkedin.com/jobs/view/111"]

    def test_multiple_lines(self):
        text = ("https://www.linkedin.com/jobs/view/1\n"
                "https://www.linkedin.com/jobs/view/2\n")
        assert parse_url_list(text) == [
            "https://www.linkedin.com/jobs/view/1",
            "https://www.linkedin.com/jobs/view/2"]

    def test_deduplicates(self):
        text = ("https://www.linkedin.com/jobs/view/1\n"
                "https://linkedin.com/jobs/view/1\n")
        assert len(parse_url_list(text)) == 1

    def test_mixed_text(self):
        text = "Check out https://www.linkedin.com/jobs/view/99 — great role"
        assert parse_url_list(text) == [
            "https://www.linkedin.com/jobs/view/99"]

    def test_empty(self):
        assert parse_url_list("") == []


# -- _find_job_posting ---------------------------------------------------------

class TestFindJobPosting:
    def test_simple_ld_json(self):
        html = textwrap.dedent("""\
            <html><head>
            <script type="application/ld+json">
            {"@type": "JobPosting", "title": "Manager"}
            </script>
            </head></html>""")
        posting = _find_job_posting(html)
        assert posting is not None
        assert posting["title"] == "Manager"

    def test_array_ld_json(self):
        html = textwrap.dedent("""\
            <script type="application/ld+json">
            [{"@type": "Organization"}, {"@type": "JobPosting", "title": "X"}]
            </script>""")
        assert _find_job_posting(html)["title"] == "X"

    def test_graph_ld_json(self):
        html = textwrap.dedent("""\
            <script type="application/ld+json">
            {"@graph": [{"@type": "JobPosting", "title": "Y"}]}
            </script>""")
        assert _find_job_posting(html)["title"] == "Y"

    def test_no_job_posting(self):
        html = '<script type="application/ld+json">{"@type": "Article"}</script>'
        assert _find_job_posting(html) is None

    def test_invalid_json_skipped(self):
        html = ('<script type="application/ld+json">NOT JSON</script>'
                '<script type="application/ld+json">'
                '{"@type": "JobPosting", "title": "Z"}</script>')
        assert _find_job_posting(html)["title"] == "Z"


# -- field extractors ----------------------------------------------------------

class TestExtractLocation:
    def test_address_dict(self):
        posting = {"jobLocation": {
            "address": {"addressLocality": "London",
                        "addressRegion": "England",
                        "addressCountry": "GB"}}}
        assert _extract_location(posting) == "London, England, GB"

    def test_multiple_locations(self):
        posting = {"jobLocation": [
            {"address": {"addressLocality": "London"}},
            {"address": {"addressLocality": "Manchester"}}]}
        assert "London" in _extract_location(posting)
        assert "Manchester" in _extract_location(posting)

    def test_name_fallback(self):
        posting = {"jobLocation": [{"name": "Remote"}]}
        assert _extract_location(posting) == "Remote"

    def test_no_location(self):
        assert _extract_location({}) == ""


class TestExtractSalary:
    def test_range(self):
        posting = {"baseSalary": {
            "currency": "GBP",
            "value": {"minValue": 50000, "maxValue": 70000,
                      "unitText": "YEAR"}}}
        result = _extract_salary(posting)
        assert "50000" in result
        assert "70000" in result
        assert "GBP" in result

    def test_min_only(self):
        posting = {"baseSalary": {
            "currency": "USD",
            "value": {"minValue": 80000, "unitText": "YEAR"}}}
        result = _extract_salary(posting)
        assert "80000" in result
        assert "+" in result

    def test_no_salary(self):
        assert _extract_salary({}) is None


class TestCleanDescription:
    def test_strips_tags(self):
        assert _clean_description("<p>Hello <b>world</b></p>") == "Hello world"

    def test_br_to_newline(self):
        assert "\n" in _clean_description("line1<br/>line2")

    def test_list_items(self):
        result = _clean_description("<ul><li>one</li><li>two</li></ul>")
        assert "one" in result
        assert "two" in result


# -- _to_job -------------------------------------------------------------------

class TestToJob:
    def test_basic(self):
        posting = {
            "title": "Hotel Manager",
            "hiringOrganization": {"name": "Grand Hotel"},
            "description": "<p>Looking for a manager</p>",
            "jobLocation": {"address": {"addressLocality": "London"}},
            "datePosted": "2026-09-01",
        }
        job = _to_job(posting, "https://www.linkedin.com/jobs/view/42", "42")
        assert job.title == "Hotel Manager"
        assert job.company == "Grand Hotel"
        assert job.provider == "linkedin-import"
        assert job.provider_job_id == "42"
        assert "London" in job.locations[0]

    def test_string_org(self):
        posting = {
            "title": "Role",
            "hiringOrganization": "ACME Corp",
            "description": "",
        }
        job = _to_job(posting, "https://www.linkedin.com/jobs/view/1", "1")
        assert job.company == "ACME Corp"


# -- DailyCounter --------------------------------------------------------------

def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


class TestDailyCounter:
    def test_starts_at_zero(self):
        conn = _mem_conn()
        counter = _DailyCounter(conn)
        assert counter.used_today() == 0
        assert counter.remaining() == DAILY_IMPORT_CAP

    def test_increment(self):
        conn = _mem_conn()
        counter = _DailyCounter(conn)
        counter.increment()
        assert counter.used_today() == 1
        assert counter.remaining() == DAILY_IMPORT_CAP - 1

    def test_check_passes(self):
        conn = _mem_conn()
        counter = _DailyCounter(conn)
        counter.check(5)

    def test_check_raises_at_cap(self):
        conn = _mem_conn()
        counter = _DailyCounter(conn)
        for _ in range(DAILY_IMPORT_CAP):
            counter.increment()
        with pytest.raises(DailyCapExceeded) as exc_info:
            counter.check()
        assert exc_info.value.used == DAILY_IMPORT_CAP
        assert exc_info.value.cap == DAILY_IMPORT_CAP

    def test_check_batch_exceeds(self):
        conn = _mem_conn()
        counter = _DailyCounter(conn)
        for _ in range(DAILY_IMPORT_CAP - 2):
            counter.increment()
        with pytest.raises(DailyCapExceeded):
            counter.check(5)


# -- ImportResult --------------------------------------------------------------

class TestImportResult:
    def test_ok_with_job(self):
        posting = {"title": "X", "description": "", "hiringOrganization": "Y"}
        job = _to_job(posting, "https://www.linkedin.com/jobs/view/1", "1")
        r = ImportResult(job=job, url="https://www.linkedin.com/jobs/view/1")
        assert r.ok

    def test_not_ok_with_error(self):
        r = ImportResult(url="bad", error="not a URL")
        assert not r.ok

    def test_not_ok_without_job(self):
        r = ImportResult(url="something")
        assert not r.ok
