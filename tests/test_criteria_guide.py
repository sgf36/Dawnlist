"""Tests for the criteria guide .docx export and reimport."""
from __future__ import annotations

import json

import pytest

from app.core.criteria_guide import (
    GuideChanges, GuideData, diff_guide, export_guide, extract_sections,
    parse_searches_table, read_guide, _format_factsheet, _parse_scope_line,
    MARKER_BRIEF, MARKER_FACTSHEET, MARKER_SCOPE, MARKER_SEARCHES,
)


# -- fixtures ----------------------------------------------------------------

def _sample_factsheet() -> str:
    return json.dumps({
        "roles": [{
            "employer": "Acme Hotels",
            "title_variants": [
                {"title": "Senior Analyst", "source_document": "CV1"},
                {"title": "Investment Analyst", "source_document": "CV2"},
            ],
            "started": "2022",
            "ended": "2024",
            "claims": [{
                "statement": "Managed a portfolio of 14 properties",
                "verb": "managed",
                "figure": "£2.3M",
                "source_document": "CV1",
            }],
        }],
        "must_never_claim": [
            "RICS qualification",
            "Direct line management of more than 3 reports",
        ],
        "open_questions": ["Exact salary at Acme Hotels"],
    })


def _sample_data() -> GuideData:
    return GuideData(
        factsheet=_sample_factsheet(),
        fit_brief="Look for hotel asset management roles at the Senior Associate to Manager level in London.",
        queries=[
            ("Hotel Asset Manager", ["Asset Manager", "Hotel AM"], True, "title"),
            ("Investment Analyst", ["Investment Analyst"], False, "both"),
        ],
        scope_countries=["GB"],
        scope_cities=["London"],
        scope_employment_types=["full_time"],
        scope_exclude_title_terms=["intern", "trainee"],
        scope_exclude_companies=["Acme Hotels"],
        generated_at="2026-09-16 10:00 UTC",
    )


# -- export ------------------------------------------------------------------

def test_export_creates_valid_docx(tmp_path):
    dest = tmp_path / "guide.docx"
    export_guide(_sample_data(), dest)
    assert dest.exists()
    assert dest.stat().st_size > 1000


def test_export_is_a4(tmp_path):
    from docx import Document
    from docx.shared import Mm
    dest = tmp_path / "guide.docx"
    export_guide(_sample_data(), dest)
    doc = Document(str(dest))
    section = doc.sections[0]
    # A4 = 210 × 297 mm; check within 1mm tolerance
    assert abs(section.page_width - Mm(210)) < Mm(1)
    assert abs(section.page_height - Mm(297)) < Mm(1)


def test_export_contains_all_sections(tmp_path):
    dest = tmp_path / "guide.docx"
    export_guide(_sample_data(), dest)
    text = read_guide(dest)
    assert MARKER_FACTSHEET in text
    assert MARKER_BRIEF in text
    assert MARKER_SEARCHES in text
    assert MARKER_SCOPE in text


def test_export_contains_queries(tmp_path):
    dest = tmp_path / "guide.docx"
    export_guide(_sample_data(), dest)
    text = read_guide(dest)
    assert "Hotel Asset Manager" in text
    assert "Investment Analyst" in text


def test_export_contains_scope(tmp_path):
    dest = tmp_path / "guide.docx"
    export_guide(_sample_data(), dest)
    text = read_guide(dest)
    assert "GB" in text
    assert "London" in text


def test_export_empty_data(tmp_path):
    """An export with no data should still produce a valid document."""
    dest = tmp_path / "guide.docx"
    export_guide(GuideData(generated_at="2026-01-01"), dest)
    assert dest.exists()
    text = read_guide(dest)
    assert "No fit brief generated yet" in text


# -- format_factsheet -------------------------------------------------------

def test_format_factsheet_json():
    rendered = _format_factsheet(_sample_factsheet())
    assert "Acme Hotels" in rendered
    assert "Senior Analyst" in rendered
    assert "RICS qualification" in rendered
    assert "managed" in rendered


def test_format_factsheet_plain_text():
    """Non-JSON factsheets pass through as-is."""
    assert _format_factsheet("Just plain text.") == "Just plain text."


def test_format_factsheet_empty():
    result = _format_factsheet("")
    assert "No factsheet" in result


# -- read_guide --------------------------------------------------------------

def test_read_guide_round_trips(tmp_path):
    dest = tmp_path / "guide.docx"
    data = _sample_data()
    export_guide(data, dest)
    text = read_guide(dest)
    assert isinstance(text, str)
    assert len(text) > 100


# -- extract_sections --------------------------------------------------------

def test_extract_sections():
    text = (
        f"Intro\n1. {MARKER_FACTSHEET}\nFactsheet content here.\n"
        f"2. {MARKER_BRIEF}\nBrief content here.\n"
        f"3. {MARKER_SEARCHES}\nSearch table here.\n"
        f"4. {MARKER_SCOPE}\nScope content here.\n"
    )
    sections = extract_sections(text)
    assert MARKER_FACTSHEET in sections
    assert "Factsheet content" in sections[MARKER_FACTSHEET]
    assert MARKER_BRIEF in sections
    assert "Brief content" in sections[MARKER_BRIEF]


# -- parse_searches_table ----------------------------------------------------

def test_parse_searches_table():
    text = (
        "Search name\tJob titles\tType\tEnabled\n"
        "Hotel AM\tAsset Manager; Hotel AM\ttitle\tYes\n"
        "Analyst\tInvestment Analyst\tboth\tNo\n"
    )
    rows = parse_searches_table(text)
    assert len(rows) == 2
    assert rows[0] == ("Hotel AM", ["Asset Manager", "Hotel AM"], "title", True)
    assert rows[1] == ("Analyst", ["Investment Analyst"], "both", False)


def test_parse_searches_table_bad_type_defaults():
    text = "Name\tTitles\tType\n" "A\tManager\tinvalid\n"
    rows = parse_searches_table(text)
    assert rows[0][2] == "title"


def test_parse_searches_table_minimal():
    text = "Name\tTitles\tType\n" "A\tManager\ttitle\n"
    rows = parse_searches_table(text)
    assert len(rows) == 1
    assert rows[0][3] is False  # default disabled


# -- _parse_scope_line -------------------------------------------------------

def test_parse_scope_line():
    text = "Countries: GB, US\nCities: London, New York"
    assert _parse_scope_line(text, "Countries") == ["GB", "US"]
    assert _parse_scope_line(text, "Cities") == ["London", "New York"]


def test_parse_scope_line_none_set():
    text = "Countries: (none set)"
    assert _parse_scope_line(text, "Countries") == []


def test_parse_scope_line_missing():
    text = "Something else entirely"
    assert _parse_scope_line(text, "Countries") is None


# -- diff_guide --------------------------------------------------------------

def test_diff_no_changes(tmp_path):
    data = _sample_data()
    dest = tmp_path / "guide.docx"
    export_guide(data, dest)
    text = read_guide(dest)
    changes = diff_guide(data, text)
    assert not changes.has_changes


def test_diff_detects_added_query(tmp_path):
    data = _sample_data()
    dest = tmp_path / "guide.docx"
    export_guide(data, dest)

    # Simulate adding a query to the imported text
    text = read_guide(dest)
    text = text.replace(
        "Investment Analyst\tInvestment Analyst\tboth\tNo",
        "Investment Analyst\tInvestment Analyst\tboth\tNo\n"
        "New Search\tNew Title; Another Title\tdescription\tNo",
    )
    changes = diff_guide(data, text)
    assert len(changes.queries_added) == 1
    assert changes.queries_added[0][0] == "New Search"


def test_diff_detects_removed_query(tmp_path):
    data = _sample_data()
    dest = tmp_path / "guide.docx"
    export_guide(data, dest)

    text = read_guide(dest)
    # Remove the Investment Analyst row
    text = text.replace(
        "Investment Analyst\tInvestment Analyst\tboth\tNo", "")
    changes = diff_guide(data, text)
    assert "Investment Analyst" in changes.queries_removed


def test_diff_detects_scope_change(tmp_path):
    data = _sample_data()
    dest = tmp_path / "guide.docx"
    export_guide(data, dest)

    text = read_guide(dest)
    text = text.replace("Countries: GB", "Countries: GB, US")
    changes = diff_guide(data, text)
    assert changes.scope_countries == ["GB", "US"]


def test_diff_detects_brief_change(tmp_path):
    data = _sample_data()
    dest = tmp_path / "guide.docx"
    export_guide(data, dest)

    text = read_guide(dest)
    text = text.replace(
        "Look for hotel asset management roles",
        "Look for hotel asset management AND development roles",
    )
    changes = diff_guide(data, text)
    assert changes.fit_brief is not None
    assert "development" in changes.fit_brief
