"""Tests for the CSV import/export module."""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pytest

from app.core.query_csv import (CELL_SEP, COLUMNS, ImportError_, QueryRow,
                                 export_queries, parse_import, write_template)


# -- template ----------------------------------------------------------------

def test_template_has_all_columns(tmp_path):
    dest = tmp_path / "t.csv"
    write_template(dest)
    header = dest.read_text(encoding="utf-8-sig").splitlines()[0]
    for col in COLUMNS:
        assert col in header


def test_template_rows_are_disabled(tmp_path):
    dest = tmp_path / "t.csv"
    write_template(dest)
    rows = parse_import(dest)
    assert all(not r.enabled for r in rows)


def test_template_has_search_type_column(tmp_path):
    dest = tmp_path / "t.csv"
    write_template(dest)
    rows = parse_import(dest)
    assert rows[0].search_type == "title"
    assert rows[1].search_type == "both"


# -- parse_import ------------------------------------------------------------

def test_minimal_csv():
    csv = io.StringIO("label,titles\nHotel Manager,Hotel Manager\n")
    rows = parse_import(csv)
    assert len(rows) == 1
    assert rows[0].label == "Hotel Manager"
    assert rows[0].titles == ["Hotel Manager"]
    assert rows[0].search_type == "title"  # default
    assert rows[0].enabled is False  # default


def test_search_type_column():
    csv = io.StringIO(
        "label,titles,search_type\n"
        "A,Manager,description\n"
        "B,Director,both\n"
        "C,VP,title\n")
    rows = parse_import(csv)
    assert rows[0].search_type == "description"
    assert rows[1].search_type == "both"
    assert rows[2].search_type == "title"


def test_bad_search_type_rejected():
    csv = io.StringIO(
        "label,titles,search_type\nA,Manager,keyword\n")
    with pytest.raises(ImportError_, match="search_type"):
        parse_import(csv)


def test_semicolons_split_titles():
    csv = io.StringIO("label,titles\nA,Hotel Manager;Operations Manager\n")
    rows = parse_import(csv)
    assert rows[0].titles == ["Hotel Manager", "Operations Manager"]


def test_exclude_columns():
    csv = io.StringIO(
        "label,titles,exclude_title_terms,exclude_companies\n"
        "A,Manager,intern;trainee,BigCorp;SmallCo\n")
    rows = parse_import(csv)
    assert rows[0].exclude_title_terms == ["intern", "trainee"]
    assert rows[0].exclude_companies == ["BigCorp", "SmallCo"]


def test_empty_exclude_columns():
    csv = io.StringIO(
        "label,titles,exclude_title_terms,exclude_companies\n"
        "A,Manager,,\n")
    rows = parse_import(csv)
    assert rows[0].exclude_title_terms == []
    assert rows[0].exclude_companies == []


def test_empty_label_rejected():
    csv = io.StringIO("label,titles\n,Hotel Manager\n")
    with pytest.raises(ImportError_, match="label is empty"):
        parse_import(csv)


def test_empty_titles_rejected():
    csv = io.StringIO("label,titles\nA,\n")
    with pytest.raises(ImportError_, match="at least one title"):
        parse_import(csv)


def test_posted_within_days_validated():
    csv = io.StringIO("label,titles,posted_within_days\nA,M,0\n")
    with pytest.raises(ImportError_, match="posted_within_days"):
        parse_import(csv)

    csv = io.StringIO("label,titles,posted_within_days\nA,M,91\n")
    with pytest.raises(ImportError_, match="posted_within_days"):
        parse_import(csv)


def test_empty_file():
    csv = io.StringIO("")
    with pytest.raises(ImportError_, match="empty"):
        parse_import(csv)


def test_header_only():
    csv = io.StringIO("label,titles\n")
    with pytest.raises(ImportError_, match="no data rows"):
        parse_import(csv)


def test_missing_required_column():
    csv = io.StringIO("label,countries\nA,GB\n")
    with pytest.raises(ImportError_, match="titles"):
        parse_import(csv)


def test_enabled_values():
    csv = io.StringIO(
        "label,titles,enabled\n"
        "A,M,1\nB,M,true\nC,M,yes\nD,M,on\n"
        "E,M,0\nF,M,false\nG,M,no\n")
    rows = parse_import(csv)
    assert [r.enabled for r in rows] == [
        True, True, True, True, False, False, False]


# -- export ------------------------------------------------------------------

def test_export_round_trips(tmp_path):
    rows = [("Hotel Manager", ["Hotel Manager"], True, "title"),
            ("Ops", ["Operations Manager", "Ops Lead"], False, "both")]
    dest = tmp_path / "out.csv"
    export_queries(rows, dest, scope_params={
        "countries": ["GB"], "cities": ["London"],
        "exclude_title_terms": ["intern"], "exclude_companies": ["BigCo"]})
    reimported = parse_import(dest)
    assert len(reimported) == 2
    assert reimported[0].label == "Hotel Manager"
    assert reimported[0].search_type == "title"
    assert reimported[1].search_type == "both"


def test_export_3_tuple_compat(tmp_path):
    """Old callers may still pass 3-tuples without search_type."""
    rows = [("A", ["Manager"], True)]
    dest = tmp_path / "out.csv"
    export_queries(rows, dest)
    reimported = parse_import(dest)
    assert reimported[0].search_type == "title"
