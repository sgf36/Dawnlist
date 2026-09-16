"""CSV import and export for saved searches.

A spreadsheet is the fastest way for someone with twenty searches to enter and
a clear picture of what they want. The template is downloadable from the app,
so the column names are never guessed.

IMPORT SAFETY
-------------
Imported queries start DISABLED unless the CSV explicitly says ``enabled=1``.
Billing is per posting returned, and a bulk import that silently enables forty
queries would spend real money the moment the morning run fires. The column
exists for round-tripping an export, not for defaulting to "on".

The label column is the primary key (UNIQUE in the database), so re-importing
an edited CSV updates in place rather than duplicating.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

COLUMNS = ("label", "titles", "search_type", "countries", "cities",
           "posted_within_days", "exclude_title_terms", "exclude_companies",
           "enabled")

#: Delimiter inside a cell: titles and countries are multi-valued. Semicolons
#: are unambiguous in every locale that Excel ships with, and do not collide
#: with the CSV delimiter itself.
CELL_SEP = ";"

#: Allowed values for search_type. "title" matches job titles, "description"
#: matches job descriptions, "both" matches either. Title-only is the default
#: because it is the cheapest shape: fewer rows returned per credit, higher
#: relevance, lower billing.
SEARCH_TYPES = {"title", "description", "both"}

TEMPLATE_ROWS = [
    {"label": "Hotel Manager",
     "titles": "Hotel Manager",
     "search_type": "title",
     "countries": "GB",
     "cities": "London",
     "posted_within_days": "14",
     "exclude_title_terms": "",
     "exclude_companies": "",
     "enabled": "0"},
    {"label": "Hospitality Operations",
     "titles": "Hospitality Manager;Operations Manager Hotel",
     "search_type": "both",
     "countries": "GB;US",
     "cities": "",
     "posted_within_days": "14",
     "exclude_title_terms": "intern;trainee",
     "exclude_companies": "",
     "enabled": "0"},
]


@dataclass
class QueryRow:
    """One parsed row from a CSV import."""

    label: str
    titles: list[str]
    search_type: str
    countries: list[str]
    cities: list[str]
    posted_within_days: int
    exclude_title_terms: list[str]
    exclude_companies: list[str]
    enabled: bool


class ImportError_(ValueError):
    """A row that cannot be used, with context for the user."""


def write_template(dest: str | Path) -> Path:
    """Write a ready-to-fill CSV template. Returns the path written."""
    dest = Path(dest)
    with open(dest, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in TEMPLATE_ROWS:
            writer.writerow(row)
    return dest


def export_queries(rows: list[tuple], dest: str | Path, *,
                   scope_params: dict | None = None) -> Path:
    """Export the current saved searches to a CSV.

    `rows` is ``(label, titles, enabled, search_type)`` as ``all_queries``
    returns. `scope_params` is the install-wide scope dict for
    countries/cities and exclusions.
    """
    dest = Path(dest)
    countries = CELL_SEP.join(
        scope_params.get("countries", [])) if scope_params else ""
    cities = CELL_SEP.join(
        scope_params.get("cities", [])) if scope_params else ""
    exclude_titles = CELL_SEP.join(
        scope_params.get("exclude_title_terms", [])) if scope_params else ""
    exclude_cos = CELL_SEP.join(
        scope_params.get("exclude_companies", [])) if scope_params else ""
    with open(dest, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row_data in rows:
            label = row_data[0]
            titles = row_data[1]
            enabled = row_data[2]
            search_type = row_data[3] if len(row_data) > 3 else "title"
            writer.writerow({
                "label": label,
                "titles": CELL_SEP.join(titles),
                "search_type": search_type,
                "countries": countries,
                "cities": cities,
                "posted_within_days": "14",
                "exclude_title_terms": exclude_titles,
                "exclude_companies": exclude_cos,
                "enabled": "1" if enabled else "0",
            })
    return dest


def parse_import(source: str | Path | TextIO) -> list[QueryRow]:
    """Parse a CSV file into validated query rows.

    Raises `ImportError_` for the first row that is invalid, with a
    human-readable message that names the row number and the problem.
    """
    if isinstance(source, (str, Path)):
        f = open(source, newline="", encoding="utf-8-sig")
        close = True
    else:
        f = source
        close = False

    try:
        reader = csv.DictReader(f)

        # Validate header
        if reader.fieldnames is None:
            raise ImportError_("The file is empty.")
        present = {h.strip().lower() for h in reader.fieldnames}
        missing = {"label", "titles"} - present
        if missing:
            raise ImportError_(
                f"Required column(s) missing: {', '.join(sorted(missing))}. "
                f"Download the template for the expected format.")

        rows: list[QueryRow] = []
        for i, raw in enumerate(reader, start=2):  # row 1 is the header
            label = (raw.get("label") or "").strip()
            if not label:
                raise ImportError_(f"Row {i}: label is empty.")

            titles_raw = (raw.get("titles") or "").strip()
            titles = [t.strip() for t in titles_raw.split(CELL_SEP)
                      if t.strip()]
            if not titles:
                raise ImportError_(
                    f"Row {i} ({label}): at least one title is required.")

            # search_type: defaults to "title" when absent or blank
            st_raw = (raw.get("search_type") or "title").strip().lower()
            if st_raw not in SEARCH_TYPES:
                raise ImportError_(
                    f"Row {i} ({label}): search_type must be one of "
                    f"title, description, both — got '{st_raw}'.")
            search_type = st_raw

            countries = [c.strip().upper()
                         for c in (raw.get("countries") or "").split(CELL_SEP)
                         if c.strip()]

            cities = [c.strip()
                      for c in (raw.get("cities") or "").split(CELL_SEP)
                      if c.strip()]

            pwd_raw = (raw.get("posted_within_days") or "14").strip()
            try:
                posted_within_days = int(pwd_raw)
                if posted_within_days < 1 or posted_within_days > 90:
                    raise ValueError
            except ValueError:
                raise ImportError_(
                    f"Row {i} ({label}): posted_within_days must be a number "
                    f"between 1 and 90, got '{pwd_raw}'.")

            exclude_title_terms = [
                t.strip()
                for t in (raw.get("exclude_title_terms") or "").split(CELL_SEP)
                if t.strip()]

            exclude_companies = [
                c.strip()
                for c in (raw.get("exclude_companies") or "").split(CELL_SEP)
                if c.strip()]

            enabled_raw = (raw.get("enabled") or "0").strip()
            enabled = enabled_raw in ("1", "true", "yes", "on")

            rows.append(QueryRow(
                label=label, titles=titles, search_type=search_type,
                countries=countries, cities=cities,
                posted_within_days=posted_within_days,
                exclude_title_terms=exclude_title_terms,
                exclude_companies=exclude_companies,
                enabled=enabled))

        if not rows:
            raise ImportError_(
                "The file has a header but no data rows. "
                "Add at least one search.")

        return rows
    finally:
        if close:
            f.close()
