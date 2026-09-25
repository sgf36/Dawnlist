"""Generate a consolidated handwritten-letter drafts .docx.

A4 portrait, one letter per page. Each page has a recipient address block
and the letter body (~90 words). The Notes column and any placeholder text
are handled by the caller — this module only lays out what it is given.

Output is verified A4 portrait: 11906 × 16838 twips, margins pinned at 25mm.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Mm, Pt, Twips
from docx.oxml.ns import qn

A4_WIDTH = Mm(210)
A4_HEIGHT = Mm(297)
A4_MARGIN = Mm(25)

A4_WIDTH_TWIPS = 11906
A4_HEIGHT_TWIPS = 16838


@dataclass
class LetterSpec:
    recipient_name: str
    recipient_title: str
    company: str
    address_lines: list[str]
    body: str
    opportunity_id: int
    contact_id: int | None = None


def build_letters_docx(letters: list[LetterSpec], out: Path) -> Path:
    """Render one page per letter into a single .docx file."""
    if not letters:
        raise ValueError("No letters to render")

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    style.paragraph_format.space_after = Pt(0)

    for s in doc.sections:
        s.page_width = A4_WIDTH
        s.page_height = A4_HEIGHT
        s.left_margin = s.right_margin = A4_MARGIN
        s.top_margin = s.bottom_margin = A4_MARGIN

    for i, letter in enumerate(letters):
        if i > 0:
            doc.add_page_break()

        _add_address_block(doc, letter)
        doc.add_paragraph()
        _add_body(doc, letter)

    doc.save(str(out))
    _verify_geometry(out)
    return out


def _add_address_block(doc: Document, letter: LetterSpec) -> None:
    if letter.recipient_name:
        name_line = letter.recipient_name
        if letter.recipient_title:
            name_line = f"{letter.recipient_title} {name_line}"
        p = doc.add_paragraph()
        run = p.add_run(name_line)
        run.bold = True
        run.font.size = Pt(11)

    if letter.company:
        p = doc.add_paragraph()
        run = p.add_run(letter.company)
        run.font.size = Pt(11)

    for line in letter.address_lines:
        if line and line.strip():
            p = doc.add_paragraph()
            run = p.add_run(line.strip())
            run.font.size = Pt(11)


def _add_body(doc: Document, letter: LetterSpec) -> None:
    for para_text in letter.body.split("\n\n"):
        para_text = para_text.strip()
        if not para_text:
            continue
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(6)
        run = p.add_run(para_text)
        run.font.size = Pt(11)


def _verify_geometry(path: Path) -> None:
    doc = Document(str(path))
    sec = doc.sections[0]
    w = round(sec.page_width / 635)
    h = round(sec.page_height / 635)
    if (w, h) != (A4_WIDTH_TWIPS, A4_HEIGHT_TWIPS):
        raise AssertionError(
            f"Letter .docx geometry {w}×{h}, expected "
            f"{A4_WIDTH_TWIPS}×{A4_HEIGHT_TWIPS}")
    margin_twips = round(Mm(25) / Twips(1))
    left = round(sec.left_margin / Twips(1))
    right = round(sec.right_margin / Twips(1))
    if abs(left - margin_twips) > 5 or abs(right - margin_twips) > 5:
        raise AssertionError(
            f"Letter .docx margins {left}/{right}, expected ~{margin_twips}")


def letters_due(conn: sqlite3.Connection, as_of: date | None = None
                ) -> list[LetterSpec]:
    """Query tasks due today whose channel implies a letter."""
    as_of = as_of or date.today()
    rows = conn.execute("""
        SELECT t.id, t.opportunity_id, t.title, t.due_on,
               o.company,
               c.id AS contact_id, c.name, c.first_name, c.last_name,
               c.job_title, c.mailing_address
        FROM tasks t
        JOIN opportunities o ON o.id = t.opportunity_id
        LEFT JOIN contacts c ON c.opportunity_id = o.id
                            AND c.do_not_contact = 0
        WHERE t.status IN ('open', 'waiting')
          AND t.due_on <= ?
          AND t.title LIKE '%letter%'
        ORDER BY t.due_on, o.company
    """, (as_of.isoformat(),)).fetchall()

    specs = []
    for r in rows:
        name = r["name"] or ""
        if r["first_name"] and r["last_name"]:
            name = f"{r['first_name']} {r['last_name']}"

        address_lines = []
        if r["mailing_address"]:
            address_lines = [
                ln.strip() for ln in r["mailing_address"].split("\n")
                if ln.strip()
            ]

        specs.append(LetterSpec(
            recipient_name=name,
            recipient_title=r["job_title"] or "",
            company=r["company"],
            address_lines=address_lines,
            body="",
            opportunity_id=r["opportunity_id"],
            contact_id=r["contact_id"],
        ))

    return specs
