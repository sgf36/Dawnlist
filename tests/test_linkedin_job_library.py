"""Tests for the LinkedIn Job Library adapter."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from unittest.mock import patch

import pytest

from app.linkedin.job_library import (
    PROVIDER_NAME,
    LinkedInJobLibraryProvider,
    _build_params,
    _encode_query,
    _country_urn,
    _parse_salary,
    _to_job,
)
from app.core.dedup import cross_provider_merge
from app.feed.base import SearchQuery
from app.feed.models import Job


# -- country URN mapping ---------------------------------------------------

def test_country_urn_gb():
    assert _country_urn("gb") == "urn:li:country:gb"

def test_country_urn_uk_maps_to_gb():
    assert _country_urn("UK") == "urn:li:country:gb"

def test_country_urn_unknown():
    assert _country_urn("za") == "urn:li:country:za"


# -- query parameter building ---------------------------------------------

def test_build_params_keyword_is_one_title_never_a_joined_string():
    """LinkedIn ANDs the words of one keyword: 'Product Manager Product
    Strategy' matched 357k postings, fewer than either title alone."""
    q = SearchQuery(label="test", titles=["Hotel Manager", "Resort Manager"])
    p = _build_params(q, 0, 24)
    assert p["keyword"] == "Hotel Manager"
    assert _build_params(q, 0, 24, "Resort Manager")["keyword"] == "Resort Manager"
    assert p["q"] == "criteria"
    assert p["start"] == "0"
    assert p["count"] == "24"

def test_build_params_countries():
    q = SearchQuery(label="test", titles=["PM"], countries=["gb", "us"])
    p = _build_params(q, 0, 24)
    assert "urn:li:country:gb" in p["countries"]
    assert "urn:li:country:us" in p["countries"]

def test_build_params_organization():
    q = SearchQuery(label="test", companies=["Marriott", "Hilton"])
    p = _build_params(q, 0, 24)
    assert p["organization"] == "Marriott"

def test_build_params_pagination():
    q = SearchQuery(label="test", titles=["PM"])
    p = _build_params(q, 48, 24)
    assert p["start"] == "48"
    assert p["count"] == "24"

def test_description_keywords_are_not_ANDed_into_the_title_query():
    q = SearchQuery(label="test", titles=["Manager"],
                    description_keywords=["asset", "portfolio"])
    assert _build_params(q, 0, 24)["keyword"] == "Manager"
    # a search with no titles falls back to the description keywords
    from app.linkedin.job_library import _keywords_for
    assert _keywords_for(SearchQuery(label="t", description_keywords=["asset", "fund"])) == ["asset", "fund"]

def test_build_params_no_titles_no_keyword():
    q = SearchQuery(label="test", countries=["gb"])
    p = _build_params(q, 0, 24)
    assert "keyword" not in p

def test_build_params_sends_no_sort_params():
    # The live API rejects sortBy.* with QUERY_PARAM_NOT_ALLOWED.
    q = SearchQuery(label="t", titles=["PM"])
    p = _build_params(q, 0, 24)
    assert not any(k.startswith("sortBy") for k in p)


def test_encode_query_matches_the_form_the_live_api_accepts():
    q = SearchQuery(label="t", titles=["Hotel Manager"], countries=["gb"])
    qs = _encode_query(_build_params(q, 0, 24))
    assert "countries=List(urn%3Ali%3Acountry%3Agb)" in qs
    assert "keyword=Hotel%20Manager" in qs
    assert "(value:" not in qs


def test_search_without_keyword_is_refused_locally():
    from app.linkedin.job_library import LinkedInJobLibraryProvider
    res = LinkedInJobLibraryProvider("tok").search(
        SearchQuery(label="t", countries=["gb"]))
    assert not res.ok and res.pages_fetched == 0


def test_parse_salary_range():
    assert _parse_salary({
        "minBaseSalary": "100000", "maxBaseSalary": "150000",
        "currencyCode": "USD", "payPeriod": "YEARLY"
    }) == "USD 100000–150000"

def test_parse_salary_hourly():
    assert _parse_salary({
        "minBaseSalary": "25", "maxBaseSalary": "35",
        "currencyCode": "GBP", "payPeriod": "HOURLY"
    }) == "GBP 25–35 (hourly)"

def test_parse_salary_min_only():
    result = _parse_salary({"minBaseSalary": "80000", "currencyCode": "EUR"})
    assert result == "EUR 80000+"

def test_parse_salary_none():
    assert _parse_salary(None) is None

def test_parse_salary_empty():
    assert _parse_salary({}) is None

def test_parse_salary_compensation_period_prefix():
    result = _parse_salary({
        "minBaseSalary": "50", "maxBaseSalary": "70",
        "currencyCode": "USD", "payPeriod": "CompensationPeriod_HOURLY"
    })
    assert "(hourly)" in result


# -- job parsing -----------------------------------------------------------

SAMPLE_ELEMENT = {
    "jobPostingUrl": "https://www.linkedin.com/ad-library/job/detail/4467288299",
    "restrictionReason": "This information is only available for restricted jobs.",
    "isRestricted": False,
    "jobDetails": {
        "jobTitle": "Hotel General Manager",
        "jobLocation": "London, England, United Kingdom",
        "organizationName": "Marriott International",
        "organizationUrl": "https://www.linkedin.com/company/12345",
        "jobDescription": "We are looking for an experienced Hotel General Manager...",
        "payerName": "Marriott International",
        "jobApplyMethod": "Complex Onsite",
        "jobBenefits": ["Medical insurance", "Dental insurance"],
        "jobSalaryRange": {
            "currencyCode": "GBP",
            "minBaseSalary": "80000",
            "maxBaseSalary": "120000",
            "payPeriod": "YEARLY",
        },
        "jobListTimeInMilliseconds": 1695945600000,
        "jobClosedTimeInMilliseconds": None,
        "jobStatistics": {
            "totalImpressions": {"start": 0, "end": 1000},
            "impressionsPerCountry": [
                {"country": "urn:li:country:gb", "impressionPercentage": 85.0}
            ],
        },
        "jobTargeting": [
            {
                "facetName": "Location",
                "isIncluded": True,
                "includedSegments": ["United Kingdom"],
                "isExcluded": False,
                "excludedSegments": [],
            }
        ],
    },
}


def test_to_job_basic():
    job = _to_job(SAMPLE_ELEMENT)
    assert job is not None
    assert job.provider == PROVIDER_NAME
    assert job.provider_job_id == "4467288299"
    assert job.title == "Hotel General Manager"
    assert job.company == "Marriott International"
    assert job.locations == ("London, England, United Kingdom",)
    assert "experienced Hotel General Manager" in job.description_text
    assert job.url == "https://www.linkedin.com/ad-library/job/detail/4467288299"

def test_to_job_salary():
    job = _to_job(SAMPLE_ELEMENT)
    assert job.salary == "GBP 80000–120000"

def test_to_job_raw_criteria():
    job = _to_job(SAMPLE_ELEMENT)
    assert job.raw_criteria["target_locations"] == ["United Kingdom"]
    assert job.raw_criteria["payer"] == "Marriott International"
    assert job.raw_criteria["apply_method"] == "Complex Onsite"
    assert job.raw_criteria["benefits"] == ["Medical insurance", "Dental insurance"]
    assert "impressions" in job.raw_criteria

def test_to_job_posted_at():
    job = _to_job(SAMPLE_ELEMENT)
    assert job.posted_at == date(2023, 9, 29)

def test_to_job_id_from_url():
    job = _to_job(SAMPLE_ELEMENT)
    assert job.provider_job_id == "4467288299"

def test_to_job_restricted_skipped():
    el = {**SAMPLE_ELEMENT, "isRestricted": True}
    # _to_job still parses, but the provider's search loop skips restricted
    job = _to_job(el)
    assert job is not None  # _to_job itself doesn't filter

def test_to_job_restricted_title():
    el = {
        "jobPostingUrl": "https://www.linkedin.com/ad-library/job/detail/999",
        "jobDetails": {
            "jobTitle": "This information is not available for restricted jobs",
            "jobLocation": "London",
            "organizationName": "Test Corp",
        },
    }
    assert _to_job(el) is None

def test_to_job_no_details():
    assert _to_job({"jobPostingUrl": "http://example.com"}) is None

def test_to_job_no_salary():
    el = {
        "jobPostingUrl": "https://www.linkedin.com/ad-library/job/detail/123",
        "jobDetails": {
            "jobTitle": "Manager",
            "jobLocation": "NYC",
            "organizationName": "Acme",
        },
    }
    job = _to_job(el)
    assert job.salary is None


# -- provider construction ------------------------------------------------

def test_provider_requires_token():
    with pytest.raises(ValueError, match="token"):
        LinkedInJobLibraryProvider("")

def test_provider_name():
    p = LinkedInJobLibraryProvider("fake-token")
    assert p.name == PROVIDER_NAME

def test_credits_used_returns_none():
    p = LinkedInJobLibraryProvider("fake-token")
    assert p.credits_used() is None


# -- search (mocked transport) --------------------------------------------

def _mock_response(elements, total=100, has_next=True):
    resp = {
        "paging": {
            "start": 0, "count": len(elements), "total": total,
            "links": [{"rel": "next", "href": "/rest/jobLibrary?start=24"}]
                     if has_next else [],
        },
        "elements": elements,
    }
    return resp


def test_search_returns_jobs():
    provider = LinkedInJobLibraryProvider("fake-token")
    response = _mock_response([SAMPLE_ELEMENT], total=1, has_next=False)

    with patch.object(provider, "_call", return_value=(200, response)):
        q = SearchQuery(label="test", titles=["Hotel Manager"],
                        countries=["gb"], max_results=24)
        result = provider.search(q)

    assert result.ok
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "Hotel General Manager"
    assert result.matched == 1
    assert result.exhausted

def test_search_skips_restricted():
    restricted = {**SAMPLE_ELEMENT, "isRestricted": True}
    provider = LinkedInJobLibraryProvider("fake-token")
    response = _mock_response([SAMPLE_ELEMENT, restricted], total=2,
                               has_next=False)

    with patch.object(provider, "_call", return_value=(200, response)):
        result = provider.search(
            SearchQuery(label="test", titles=["Hotel General Manager"],
                        max_results=24))

    assert len(result.jobs) == 1

def test_search_401_returns_refusal():
    provider = LinkedInJobLibraryProvider("expired-token")

    with patch.object(provider, "_call", return_value=(401, "Unauthorized")):
        result = provider.search(
            SearchQuery(label="test", titles=["test"], max_results=24))

    assert not result.ok
    assert result.refusal == "token_expired"
    assert "expired" in result.error

def test_search_caps_at_240():
    provider = LinkedInJobLibraryProvider("fake-token")
    response = _mock_response([SAMPLE_ELEMENT] * 24, total=500)

    call_count = 0
    def counting_call(params):
        nonlocal call_count
        call_count += 1
        if call_count > 10:
            return 200, _mock_response([], has_next=False)
        return 200, response

    with patch.object(provider, "_call", side_effect=counting_call):
        result = provider.search(
            SearchQuery(label="test", titles=["test"], max_results=1000))

    assert len(result.jobs) <= 240

def test_search_empty_result():
    provider = LinkedInJobLibraryProvider("fake-token")
    response = _mock_response([], total=0, has_next=False)

    with patch.object(provider, "_call", return_value=(200, response)):
        result = provider.search(
            SearchQuery(label="test", titles=["nope"], max_results=24))

    assert result.ok
    assert len(result.jobs) == 0
    assert result.exhausted


# -- richness score --------------------------------------------------------

def test_richness_full_posting():
    job = Job(provider="x", provider_job_id="1", title="GM", company="Acme",
              locations=("London",), description_text="A" * 500,
              posted_at=date(2026, 1, 1), salary="GBP 100k",
              url="https://example.com",
              raw_criteria={"seniority": "senior", "remote": False})
    assert job.richness_score > 0

def test_richness_sparse_posting():
    full = Job(provider="x", provider_job_id="1", title="GM", company="Acme",
               locations=("London",), description_text="A" * 500,
               posted_at=date(2026, 1, 1), salary="GBP 100k",
               url="https://example.com",
               raw_criteria={"seniority": "senior"})
    sparse = Job(provider="y", provider_job_id="2", title="GM", company="Acme",
                 description_text="Short")
    assert full.richness_score > sparse.richness_score

def test_richness_description_weight():
    long_desc = Job(provider="x", provider_job_id="1", title="GM",
                    company="Acme", description_text="A" * 1000)
    short_desc = Job(provider="y", provider_job_id="2", title="GM",
                     company="Acme", description_text="Short")
    assert long_desc.richness_score > short_desc.richness_score


# -- cross-provider merge -------------------------------------------------

def _test_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            provider_job_id TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            locations_json TEXT NOT NULL DEFAULT '[]',
            description_text TEXT NOT NULL DEFAULT '',
            posted_at TEXT,
            salary TEXT,
            url TEXT NOT NULL DEFAULT '',
            raw_criteria_json TEXT NOT NULL DEFAULT '{}',
            name_key TEXT NOT NULL DEFAULT '',
            first_seen_run INTEGER,
            funnel_status TEXT NOT NULL DEFAULT 'swept',
            screen_verdict TEXT,
            screen_tier TEXT,
            screen_reason TEXT,
            UNIQUE (provider, provider_job_id)
        );
    """)
    return conn


def _insert_job(conn, provider, job_id, title, company, desc="", salary=None,
                name_key_val=""):
    from app.feed.models import name_key as mk
    nk = name_key_val or mk(company, title)
    conn.execute(
        "INSERT INTO jobs(provider, provider_job_id, title, company, "
        "description_text, salary, name_key) VALUES(?,?,?,?,?,?,?)",
        (provider, job_id, title, company, desc, salary, nk))
    conn.commit()


def test_cross_provider_keeps_richer_new():
    conn = _test_db()
    _insert_job(conn, "theirstack", "ts-123", "Hotel Manager",
                "Marriott International", desc="Short")
    new_job = Job(provider=PROVIDER_NAME, provider_job_id="li-999",
                  title="Hotel Manager", company="Marriott International",
                  description_text="A" * 500, salary="GBP 80000–120000",
                  posted_at=date(2026, 1, 1))
    result = cross_provider_merge([new_job], conn)
    assert len(result.kept) == 1
    assert result.kept[0] is new_job
    assert len(result.superseded) == 1
    assert "supersedes" in result.superseded[0][1]


def test_cross_provider_suppresses_poorer_new():
    conn = _test_db()
    _insert_job(conn, "theirstack", "ts-123", "Hotel Manager",
                "Marriott International", desc="A" * 500, salary="GBP 80k")
    new_job = Job(provider=PROVIDER_NAME, provider_job_id="li-999",
                  title="Hotel Manager", company="Marriott International",
                  description_text="Short")
    result = cross_provider_merge([new_job], conn)
    assert len(result.kept) == 0
    assert len(result.superseded) == 1
    assert "suppressed" in result.superseded[0][1]


def test_cross_provider_same_provider_passes():
    conn = _test_db()
    _insert_job(conn, PROVIDER_NAME, "li-100", "Hotel Manager",
                "Marriott International")
    new_job = Job(provider=PROVIDER_NAME, provider_job_id="li-200",
                  title="Hotel Manager", company="Marriott International")
    result = cross_provider_merge([new_job], conn)
    assert len(result.kept) == 1


def test_cross_provider_no_match_passes():
    conn = _test_db()
    new_job = Job(provider=PROVIDER_NAME, provider_job_id="li-999",
                  title="Hotel Manager", company="Marriott International")
    result = cross_provider_merge([new_job], conn)
    assert len(result.kept) == 1
    assert len(result.superseded) == 0


def test_cross_provider_empty_input():
    conn = _test_db()
    result = cross_provider_merge([], conn)
    assert len(result.kept) == 0
    assert len(result.superseded) == 0


def test_short_page_with_next_link_keeps_paging():
    # Live API returned 23 of 24 rows on page one with more behind it; the
    # adapter used to call that the end and under-fetched every search.
    provider = LinkedInJobLibraryProvider("fake-token")
    pages = [_mock_response([_el(f"Hotel Manager {i}") for i in range(23)],
                            total=100, has_next=True),
             _mock_response([_el(f"Hotel Manager {i}") for i in range(23, 47)],
                            total=100, has_next=False)]

    with patch.object(provider, "_call", side_effect=[(200, p) for p in pages]):
        result = provider.search(SearchQuery(
            label="t", titles=["Hotel Manager"], max_results=47))

    assert len(result.jobs) == 47


# -- client-side filters: the API has no city, title or date filter ---------

def _el(title, location="London, England, United Kingdom", ms=None, org="Acme",
        desc="x"):
    import time
    return {"jobPostingUrl": f"https://www.linkedin.com/ad-library/job/detail/{abs(hash(title + location)) % 10**9}",
            "jobDetails": {"jobTitle": title, "jobLocation": location,
                           "organizationName": org, "jobDescription": desc,
                           "jobListTimeInMilliseconds": ms or int(time.time() * 1000)}}


def _search(elements, **kw):
    provider = LinkedInJobLibraryProvider("fake-token")
    resp = _mock_response(elements, total=len(elements), has_next=False)
    with patch.object(provider, "_call", return_value=(200, resp)):
        return provider.search(SearchQuery(label="t", max_results=50, **kw))


def test_title_filter_rejects_a_keyword_that_only_matched_the_description():
    r = _search([_el("Hotel Manager"), _el("Supermarket Assistant")],
                titles=["Hotel Manager"])
    assert [j.title for j in r.jobs] == ["Hotel Manager"]
    assert r.scanned == 2


def test_title_filter_treats_manager_and_management_alike_in_any_order():
    r = _search([_el("VP - European Hotel Asset Management"),
                 _el("Manager, Hotel"), _el("Hotel Chef")],
                titles=["Hotel Asset Manager", "Hotel Manager"])
    assert {j.title for j in r.jobs} == {
        "VP - European Hotel Asset Management", "Manager, Hotel"}


def test_description_search_reads_the_text_not_the_title():
    r = _search([_el("Supermarket Assistant", desc="We need a Hotel Manager to lead"),
                 _el("Cashier", desc="Tills and shelves only")],
                titles=["Hotel Manager"], search_type="description")
    assert [j.title for j in r.jobs] == ["Supermarket Assistant"]


def test_title_search_with_description_keywords_needs_one_in_the_text():
    r = _search([_el("Hotel Manager", desc="asset management experience"),
                 _el("Hotel Manager Two", desc="front desk only")],
                titles=["Hotel Manager"], description_keywords=["asset"])
    assert [j.title for j in r.jobs] == ["Hotel Manager"]


def test_both_keeps_a_title_match_or_a_description_match():
    r = _search([_el("Hotel Manager", desc="x"),
                 _el("Chef", desc="reports to the asset team"),
                 _el("Driver", desc="nothing relevant")],
                titles=["Hotel Manager"], description_keywords=["asset"],
                search_type="both")
    assert sorted(j.title for j in r.jobs) == ["Chef", "Hotel Manager"]


def test_each_title_is_its_own_request_and_results_are_merged_without_repeats():
    from unittest.mock import patch as _patch
    provider = LinkedInJobLibraryProvider("fake-token")
    seen_keywords = []

    def fake(params):
        kw = params.get("keyword")
        seen_keywords.append(kw)
        rows = {"Hotel Manager": [_el("Hotel Manager"), _el("Hotel Manager Shared")],
                "Resort Manager": [_el("Resort Manager"), _el("Hotel Manager Shared")]}[kw]
        return 200, _mock_response(rows, total=len(rows), has_next=False)

    with _patch.object(provider, "_call", side_effect=fake):
        r = provider.search(SearchQuery(label="t", max_results=50,
                                        titles=["Hotel Manager", "Resort Manager"],
                                        search_type="both"))
    assert seen_keywords == ["Hotel Manager", "Resort Manager"]
    assert len([j for j in r.jobs if j.title == "Hotel Manager Shared"]) == 1
    assert len(r.jobs) == 3


def test_at_most_three_titles_are_searched():
    from app.linkedin.job_library import MAX_KEYWORDS, _keywords_for
    q = SearchQuery(label="t", titles=[f"T{i}" for i in range(9)])
    assert len(_keywords_for(q)) == MAX_KEYWORDS


def test_city_filter_keeps_every_london_spelling_and_drops_the_rest():
    r = _search([_el("Hotel Manager", "London Area, United Kingdom"),
                 _el("Hotel Manager", "Greater London, England, United Kingdom"),
                 _el("Hotel Manager", "Cirencester, England, United Kingdom")],
                titles=["Hotel Manager"], cities=["London"])
    assert len(r.jobs) == 2


def test_city_filter_keeps_boroughs_and_districts_the_api_files_them_under():
    """Measured: ~150 of 4,800 UK postings were Greater London under a name
    other than London, and a test for the word 'London' dropped every one."""
    places_ = ["Croydon, England, United Kingdom", "Canary Wharf, England",
               "Camden Town, England, United Kingdom",
               "HA3 0AA, Harrow, England, United Kingdom",
               "London Borough of Hammersmith and Fulham, England, United Kingdom",
               "West Drayton UB7 0HJ"]
    r = _search([_el("Hotel Manager", p) for p in places_],
                titles=["Hotel Manager"], cities=["London"])
    assert len(r.jobs) == len(places_)


def test_city_filter_does_not_confuse_namesakes_or_commuter_towns():
    away = ["Watford, England, United Kingdom", "Slough, England, United Kingdom",
            "Sutton Coldfield, England, United Kingdom",
            "Kingston Upon Hull, England, United Kingdom",
            "Brentwood, England, United Kingdom"]
    r = _search([_el("Hotel Manager", p) for p in away],
                titles=["Hotel Manager"], cities=["London"])
    assert r.jobs == []


def test_a_posting_with_no_place_below_country_is_kept_and_marked():
    r = _search([_el("Hotel Manager", "United Kingdom")],
                titles=["Hotel Manager"], cities=["London"])
    assert len(r.jobs) == 1
    assert r.jobs[0].raw_criteria["location_unresolved"] is True


def test_posted_within_days_drops_old_postings():
    import time
    old = int((time.time() - 40 * 86400) * 1000)
    r = _search([_el("Hotel Manager", ms=old), _el("Hotel Manager Two")],
                titles=["Hotel Manager"], posted_within_days=14)
    assert [j.title for j in r.jobs] == ["Hotel Manager Two"]


def test_exclude_title_terms_uses_word_boundaries():
    r = _search([_el("Hotel Manager Trainee"), _el("Hotel Manager Traineeship X")],
                titles=["Hotel Manager"], exclude_title_terms=["trainee"])
    assert [j.title for j in r.jobs] == ["Hotel Manager Traineeship X"]


def test_country_tag_is_set_so_the_location_gate_can_read_it():
    r = _search([_el("Hotel Manager")], titles=["Hotel Manager"],
                countries=["gb"])
    assert r.jobs[0].raw_criteria["country_codes"] == ["GB"]


def test_page_budget_bounds_the_scan_and_says_it_was_not_exhausted():
    from app.linkedin import job_library as jl
    provider = LinkedInJobLibraryProvider("fake-token")
    page = _mock_response([_el("Cashier")] * 24, total=45000, has_next=True)
    with patch.object(provider, "_call", return_value=(200, page)) as call:
        r = provider.search(SearchQuery(label="t", titles=["Hotel Manager"],
                                        max_results=50))
    assert call.call_count == jl.PAGE_BUDGET
    assert r.jobs == [] and not r.exhausted and r.scanned == 24 * jl.PAGE_BUDGET
    assert r.shortfall  # a truncated scan is never reported as "nothing more"


def test_a_429_is_the_daily_quota_not_a_generic_failure():
    provider = LinkedInJobLibraryProvider("fake-token")
    with patch.object(provider, "_call", return_value=(429, '{"code":"TOO_MANY_REQUESTS"}')):
        result = provider.search(SearchQuery(label="t", titles=["Hotel Manager"], max_results=24))
    assert not result.ok and result.refusal == "rate_limited"
    assert "quota" in result.error and "00:00 UTC" in result.error


def test_the_default_page_budget_is_a_small_slice_of_a_shared_daily_quota():
    from app.linkedin import job_library as jl
    assert jl.PAGE_BUDGET <= 12
