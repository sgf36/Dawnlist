"""Tests for the LinkedIn boolean search query generator."""
from __future__ import annotations

import pytest

from app.feed.base import SearchQuery
from app.linkedin.query_builder import (LinkedInQuery, build_location_hint,
                                        build_search_string, from_queries)


def _q(*, label="test", titles=None, exclude_title_terms=None,
       description_keywords=None, countries=None, cities=None, **kw):
    return SearchQuery(
        label=label,
        titles=titles or [],
        countries=countries or [],
        cities=cities or [],
        exclude_title_terms=exclude_title_terms or [],
        description_keywords=description_keywords or [],
        **kw,
    )


class TestBuildSearchString:
    def test_single_title(self):
        assert build_search_string(_q(titles=["Hotel Manager"])) == \
            '"Hotel Manager"'

    def test_single_word_not_quoted(self):
        assert build_search_string(_q(titles=["Manager"])) == "Manager"

    def test_multiple_titles_or_group(self):
        result = build_search_string(_q(titles=[
            "Hotel Manager", "Resort Manager"]))
        assert result == '("Hotel Manager" OR "Resort Manager")'

    def test_exclusions_as_not(self):
        result = build_search_string(_q(
            titles=["Hotel Manager"],
            exclude_title_terms=["Assistant", "Trainee"]))
        assert result == '"Hotel Manager" NOT Assistant NOT Trainee'

    def test_description_keywords_added(self):
        result = build_search_string(_q(
            titles=["Vice President"],
            description_keywords=["asset management", "hotel"]))
        assert result == \
            '"Vice President" ("asset management" OR hotel)'

    def test_empty_query(self):
        assert build_search_string(_q()) == ""

    def test_whitespace_terms_ignored(self):
        result = build_search_string(_q(titles=["Manager", "  ", ""]))
        assert result == "Manager"

    def test_multi_word_exclusion_quoted(self):
        result = build_search_string(_q(
            titles=["Manager"],
            exclude_title_terms=["Night Manager"]))
        assert result == 'Manager NOT "Night Manager"'


class TestBuildLocationHint:
    def test_cities_and_countries(self):
        assert build_location_hint(_q(
            cities=["London"], countries=["GB"])) == "London, GB"

    def test_countries_only(self):
        assert build_location_hint(_q(countries=["GB", "AE"])) == "GB, AE"

    def test_empty(self):
        assert build_location_hint(_q()) == ""


class TestFromQueries:
    def test_skips_empty(self):
        queries = [_q(label="empty"), _q(label="real", titles=["Manager"])]
        results = from_queries(queries)
        assert len(results) == 1
        assert results[0].label == "real"

    def test_returns_linkedin_query(self):
        queries = [_q(label="Hotels", titles=["Hotel Manager"],
                       countries=["GB"], cities=["London"])]
        results = from_queries(queries)
        assert len(results) == 1
        assert isinstance(results[0], LinkedInQuery)
        assert results[0].search_string == '"Hotel Manager"'
        assert results[0].location_hint == "London, GB"
