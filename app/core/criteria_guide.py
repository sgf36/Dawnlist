"""Criteria guide — a .docx that captures how Dawnlist searches for the user.

The export builds a structured document from the user's own data: the factsheet
(their evidenced career record), the fit brief (what roles to surface), the
search queries configured in the app, and the search scope (where, on what
contract, and what to exclude). The user can revise the document in Word,
Pages, or Google Docs and reimport it for Dawnlist to parse the changes.

The factsheet section is marked READ-ONLY because it is the single source of
truth for outreach accuracy — a user who edits it there instead of through
onboarding could silently un-evidence a claim the app is still making. The
fit brief and searches are editable.

Page size is A4 (see memory feedback-document-page-size). python-docx's
Section.page_width/height are in EMU. Mm() converts millimetres to EMU
correctly; raw integers do not. The expected twip values after conversion
are 11906 × 16838.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm, Pt, RGBColor

# -- Brand palette -----------------------------------------------------------

TEAL = RGBColor(0x1E, 0x4B, 0x45)
GOLD = RGBColor(0xB0, 0x7A, 0x2E)
INK = RGBColor(0x16, 0x21, 0x2A)
CREAM = RGBColor(0xF0, 0xEC, 0xE4)

# -- Section markers the reimport looks for ----------------------------------

MARKER_FACTSHEET = "BACKGROUND FACTSHEET"
MARKER_BRIEF = "FIT BRIEF"
MARKER_SEARCHES = "SEARCH QUERIES"
MARKER_SCOPE = "SEARCH SCOPE"
MARKER_READONLY = "[This section is generated from your onboarding data and is read-only.]"
MARKER_EDITABLE = "[You may edit this section. Dawnlist will review your changes on reimport.]"

# -- A4 dimensions -----------------------------------------------------------

A4_WIDTH = Mm(210)
A4_HEIGHT = Mm(297)


@dataclass
class GuideData:
    """Everything the guide needs, gathered before the document is built."""
    factsheet: str = ""
    fit_brief: str = ""
    queries: list[tuple[str, list[str], bool, str]] = field(default_factory=list)
    scope_countries: list[str] = field(default_factory=list)
    scope_cities: list[str] = field(default_factory=list)
    scope_employment_types: list[str] = field(default_factory=list)
    scope_exclude_title_terms: list[str] = field(default_factory=list)
    scope_exclude_companies: list[str] = field(default_factory=list)
    generated_at: str = ""


def _set_run_font(run, *, size=None, bold=False, italic=False, color=None,
                  name="Calibri"):
    run.font.name = name
    if size:
        run.font.size = size
    run.font.bold = bold
    run.font.italic = italic
    if color:
        run.font.color.rgb = color


def _add_heading(doc, text: str, level: int = 1) -> None:
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = TEAL


def _add_marker(doc, text: str, *, italic: bool = True) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    _set_run_font(run, size=Pt(9), italic=italic, color=GOLD)


def _add_body(doc, text: str) -> None:
    """Add body text, splitting on double newlines into paragraphs."""
    for para in text.strip().split("\n\n"):
        p = doc.add_paragraph()
        run = p.add_run(para.strip())
        _set_run_font(run, size=Pt(11), color=INK)


def _format_factsheet(raw: str) -> str:
    """The factsheet is stored as JSON. Render it as readable prose."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw or "(No factsheet generated yet.)"

    parts: list[str] = []

    roles = data.get("roles", [])
    if roles:
        parts.append("Career history")
        parts.append("-" * 40)
        for role in roles:
            employer = role.get("employer", "Unknown")
            started = role.get("started", "?")
            ended = role.get("ended") or "present"
            titles = role.get("title_variants", [])
            title_str = ", ".join(
                f"{t['title']} ({t.get('source_document', '')})"
                for t in titles) if titles else "—"
            parts.append(f"\n{employer} ({started} – {ended})")
            parts.append(f"  Titles: {title_str}")
            claims = role.get("claims", [])
            for c in claims:
                verb = c.get("verb", "")
                figure = c.get("figure") or ""
                stmt = c.get("statement", "")
                fig_part = f" [{figure}]" if figure else ""
                parts.append(f"  • {verb}: {stmt}{fig_part}")

    never = data.get("must_never_claim", [])
    if never:
        parts.append("\n\nMust never claim")
        parts.append("-" * 40)
        for item in never:
            parts.append(f"  ✕ {item}")

    questions = data.get("open_questions", [])
    if questions:
        parts.append("\n\nOpen questions")
        parts.append("-" * 40)
        for q in questions:
            parts.append(f"  ? {q}")

    return "\n".join(parts) if parts else raw


def gather_data(conn) -> GuideData:
    """Read everything the guide needs from the database."""
    from app.core.search_scope import load_scope
    from app.main import all_queries, load_document

    factsheet = load_document(conn, "factsheet")
    fit_brief = load_document(conn, "fit_brief")
    queries = all_queries(conn)
    scope = load_scope(conn)

    return GuideData(
        factsheet=factsheet,
        fit_brief=fit_brief,
        queries=queries,
        scope_countries=list(scope.countries),
        scope_cities=list(scope.cities),
        scope_employment_types=list(scope.employment_types),
        scope_exclude_title_terms=list(scope.exclude_title_terms),
        scope_exclude_companies=list(scope.exclude_companies),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def export_guide(data: GuideData, dest: Path) -> None:
    """Build the criteria guide .docx and write it to *dest*."""
    doc = Document()

    # -- A4 page setup -------------------------------------------------------
    section = doc.sections[0]
    section.page_width = A4_WIDTH
    section.page_height = A4_HEIGHT
    section.orientation = WD_ORIENT.PORTRAIT
    section.top_margin = Mm(25)
    section.bottom_margin = Mm(25)
    section.left_margin = Mm(25)
    section.right_margin = Mm(25)

    # Verify the twip trap (feedback-document-page-size)
    assert abs(section.page_width - 7560310) < 100, (
        f"A4 width EMU wrong: {section.page_width}")
    assert abs(section.page_height - 10692130) < 100, (
        f"A4 height EMU wrong: {section.page_height}")

    # -- Title ---------------------------------------------------------------
    title = doc.add_heading("Dawnlist — Criteria Guide", level=0)
    for run in title.runs:
        run.font.color.rgb = TEAL

    p = doc.add_paragraph()
    run = p.add_run(f"Generated {data.generated_at}")
    _set_run_font(run, size=Pt(10), italic=True, color=GOLD)

    p = doc.add_paragraph()
    run = p.add_run(
        "This document captures how Dawnlist searches for roles on your behalf. "
        "You may edit the sections marked as editable, then reimport the revised "
        "document into Dawnlist for it to review and apply your changes."
    )
    _set_run_font(run, size=Pt(10), color=INK)

    # -- 1. Factsheet (read-only) --------------------------------------------
    _add_heading(doc, f"1. {MARKER_FACTSHEET}")
    _add_marker(doc, MARKER_READONLY)

    rendered = _format_factsheet(data.factsheet)
    _add_body(doc, rendered)

    # -- 2. Fit Brief (editable) ---------------------------------------------
    _add_heading(doc, f"2. {MARKER_BRIEF}")
    _add_marker(doc, MARKER_EDITABLE)

    _add_body(doc, data.fit_brief or "(No fit brief generated yet.)")

    # -- 3. Search Queries (editable) ----------------------------------------
    _add_heading(doc, f"3. {MARKER_SEARCHES}")
    _add_marker(doc, MARKER_EDITABLE)

    p = doc.add_paragraph()
    run = p.add_run(
        "Each row is one search Dawnlist runs in its daily sweep. "
        "Add, remove, or edit rows as needed. The search_type column controls "
        "whether terms match against the job title, description, or both."
    )
    _set_run_font(run, size=Pt(10), italic=True, color=INK)

    if data.queries:
        table = doc.add_table(rows=1, cols=4)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = "Light Grid Accent 1"

        # Header
        headers = ["Search name", "Job titles", "Type", "Enabled"]
        for i, hdr in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = hdr
            for run in cell.paragraphs[0].runs:
                run.font.bold = True
                run.font.size = Pt(10)

        # Data rows
        for label, titles, enabled, search_type in data.queries:
            row = table.add_row()
            row.cells[0].text = label
            row.cells[1].text = "; ".join(titles)
            row.cells[2].text = search_type
            row.cells[3].text = "Yes" if enabled else "No"
            for cell in row.cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(10)
    else:
        _add_body(doc, "(No search queries configured yet.)")

    # -- 4. Search Scope (editable) ------------------------------------------
    _add_heading(doc, f"4. {MARKER_SCOPE}")
    _add_marker(doc, MARKER_EDITABLE)

    scope_parts: list[str] = []

    if data.scope_countries:
        scope_parts.append(
            f"Countries: {', '.join(data.scope_countries)}")
    else:
        scope_parts.append("Countries: (none set)")

    if data.scope_cities:
        scope_parts.append(f"Cities: {', '.join(data.scope_cities)}")

    if data.scope_employment_types:
        scope_parts.append(
            f"Employment types: {', '.join(data.scope_employment_types)}")

    if data.scope_exclude_title_terms:
        scope_parts.append(
            f"Exclude title terms: {', '.join(data.scope_exclude_title_terms)}")

    if data.scope_exclude_companies:
        scope_parts.append(
            f"Exclude companies: {', '.join(data.scope_exclude_companies)}")

    _add_body(doc, "\n\n".join(scope_parts))

    # -- Footer note ---------------------------------------------------------
    doc.add_page_break()
    _add_heading(doc, "Notes", level=2)
    p = doc.add_paragraph()
    run = p.add_run(
        "Use this space for any notes or comments about your search criteria. "
        "Dawnlist will include these in its review when you reimport."
    )
    _set_run_font(run, size=Pt(10), italic=True, color=GOLD)

    # Save
    doc.save(str(dest))


# ---------------------------------------------------------------------------
# Reimport — extract text from a revised .docx
# ---------------------------------------------------------------------------

def read_guide(path: Path) -> str:
    """Read a .docx and return the full text, preserving section structure.

    python-docx's ``Document.paragraphs`` and ``Document.tables`` are separate
    lists; iterating them one after the other loses interleaving. Walking the
    XML body children in order preserves where each table actually sits.
    """
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    lines: list[str] = []

    for child in doc.element.body:
        if child.tag == qn("w:p"):
            lines.append(Paragraph(child, doc).text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                lines.append("\t".join(cells))
    return "\n".join(lines)


def extract_sections(text: str) -> dict[str, str]:
    """Split imported guide text into named sections by heading markers.

    Headings arrive as "1. BACKGROUND FACTSHEET" etc., so the pattern matches
    an optional number-dot prefix before each marker.
    """
    sections: dict[str, str] = {}
    markers = [MARKER_FACTSHEET, MARKER_BRIEF, MARKER_SEARCHES, MARKER_SCOPE]
    # Match optional "N. " prefix before each marker
    pattern = r"(?:\d+\.\s*)?" + (
        "(?:" + "|".join(re.escape(m) for m in markers) + ")")

    splits = list(re.finditer(pattern, text, re.IGNORECASE))
    for i, match in enumerate(splits):
        # Extract the marker name (without the number prefix)
        raw = match.group()
        name = re.sub(r"^\d+\.\s*", "", raw).upper()
        start = match.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        body = text[start:end].strip()
        # Strip the marker lines (read-only / editable)
        body = body.replace(MARKER_READONLY, "").replace(MARKER_EDITABLE, "")
        sections[name] = body.strip()

    return sections


@dataclass
class GuideChanges:
    """What a reimported guide wants to change."""
    fit_brief: str | None = None
    queries_added: list[tuple[str, list[str], str]] = field(
        default_factory=list)  # (label, titles, search_type)
    queries_removed: list[str] = field(default_factory=list)  # labels
    queries_modified: list[tuple[str, list[str], str]] = field(
        default_factory=list)  # (label, new_titles, new_type)
    scope_countries: list[str] | None = None
    scope_cities: list[str] | None = None
    scope_exclude_title_terms: list[str] | None = None
    scope_exclude_companies: list[str] | None = None
    notes: str = ""

    @property
    def has_changes(self) -> bool:
        return any([
            self.fit_brief is not None,
            self.queries_added,
            self.queries_removed,
            self.queries_modified,
            self.scope_countries is not None,
            self.scope_cities is not None,
            self.scope_exclude_title_terms is not None,
            self.scope_exclude_companies is not None,
        ])


def parse_searches_table(text: str) -> list[tuple[str, list[str], str, bool]]:
    """Parse the search queries table from reimported text.

    The table comes through as tab-separated lines. The header row is
    recognised and skipped. Returns (label, titles, search_type, enabled).
    """
    rows: list[tuple[str, list[str], str, bool]] = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 3:
            continue
        # Skip header
        if parts[0].lower() in ("search name", "label", "name"):
            continue
        label = parts[0]
        if not label:
            continue
        titles = [t.strip() for t in parts[1].split(";") if t.strip()]
        search_type = parts[2].lower() if len(parts) > 2 else "title"
        if search_type not in ("title", "description", "both"):
            search_type = "title"
        enabled = parts[3].lower() in ("yes", "1", "true", "on") if len(
            parts) > 3 else False
        rows.append((label, titles, search_type, enabled))
    return rows


def _parse_scope_line(text: str, prefix: str) -> list[str] | None:
    """Extract a comma-separated list from a scope line like 'Countries: GB, US'."""
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith(prefix.lower()):
            value = line[len(prefix):].strip().lstrip(":").strip()
            if value.lower() in ("(none set)", "(none)", ""):
                return []
            return [v.strip() for v in value.split(",") if v.strip()]
    return None


def diff_guide(original: GuideData, imported_text: str) -> GuideChanges:
    """Compare the original data with an imported guide and find changes."""
    sections = extract_sections(imported_text)
    changes = GuideChanges()

    # -- Fit brief -----------------------------------------------------------
    new_brief = sections.get(MARKER_BRIEF, "")
    if new_brief and new_brief != original.fit_brief:
        changes.fit_brief = new_brief

    # -- Searches ------------------------------------------------------------
    searches_text = sections.get(MARKER_SEARCHES, "")
    if searches_text:
        imported_queries = parse_searches_table(searches_text)
        orig_by_label = {q[0]: q for q in original.queries}
        imported_by_label = {q[0]: q for q in imported_queries}

        # Added
        for label, titles, stype, _enabled in imported_queries:
            if label not in orig_by_label:
                changes.queries_added.append((label, titles, stype))

        # Removed
        for label in orig_by_label:
            if label not in imported_by_label:
                changes.queries_removed.append(label)

        # Modified
        for label, titles, stype, _enabled in imported_queries:
            if label in orig_by_label:
                orig = orig_by_label[label]
                if titles != list(orig[1]) or stype != orig[3]:
                    changes.queries_modified.append((label, titles, stype))

    # -- Scope ---------------------------------------------------------------
    scope_text = sections.get(MARKER_SCOPE, "")
    if scope_text:
        countries = _parse_scope_line(scope_text, "Countries")
        if countries is not None and countries != original.scope_countries:
            changes.scope_countries = countries

        cities = _parse_scope_line(scope_text, "Cities")
        if cities is not None and cities != original.scope_cities:
            changes.scope_cities = cities

        excl_terms = _parse_scope_line(scope_text, "Exclude title terms")
        if (excl_terms is not None
                and excl_terms != original.scope_exclude_title_terms):
            changes.scope_exclude_title_terms = excl_terms

        excl_companies = _parse_scope_line(scope_text, "Exclude companies")
        if (excl_companies is not None
                and excl_companies != original.scope_exclude_companies):
            changes.scope_exclude_companies = excl_companies

    # -- Notes ---------------------------------------------------------------
    # Everything after the last section marker
    notes_match = re.search(r"(?:^|\n)Notes?\s*\n", imported_text,
                            re.IGNORECASE)
    if notes_match:
        changes.notes = imported_text[notes_match.end():].strip()

    return changes
