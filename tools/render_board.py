"""Render the board to a PNG so a human can LOOK at it."""
import sys
import json
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import db  # noqa: E402
from app.core.board_repo import (add_task, create_opportunity, record_bounce,  # noqa: E402
                                 record_outbound)
from app.core.cadence import Channel  # noqa: E402
from app.core.tracker import JobCategory, Stage  # noqa: E402
from app.ui.board import BoardWindow  # noqa: E402
from app.ui.board_adapter import board_rows  # noqa: E402

TUE = date(2026, 9, 8)
# Microsoft Store: 1366x768 or larger, 16:9.
STORE_SIZE = (1366, 768)

def render_hidden(widget, size):
    """Lay out and paint a widget WITHOUT putting it on screen.

    Qt paints a WA_DontShowOnScreen widget normally — real fonts, real styles —
    but never maps it to the display. Without this every render flashes a
    window on the user's desktop, which is intrusive when these run repeatedly.

    The offscreen platform plugin would also avoid the flash, but it has no
    font configuration and renders every glyph as tofu.
    """
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.resize(*size)
    widget.show()
    return widget



#: Companies in the fixture that came from a real posting, with the facts the
#: board's optional columns read. Without these the Role, Location, Salary and
#: Posted columns render EMPTY — which is what the store screenshot showed
#: until 2026-09-08, advertising four blank columns.
#:
#: Not every row has one, deliberately: `Amir Mossanen (Truist)` is a mutual
#: contact and `Marriott Feasibility` is not a posting, so both are genuinely
#: role-less. A fixture where every cell is populated would hide the fact that
#: an empty cell is normal and has to look acceptable.
FIXTURE_POSTINGS = {
    "Round Hill Capital": ("Asset Management Associate", "London",
                           "£65,000 - £75,000", "2026-09-05"),
    "Landmark Venues": ("Head of Events", "London", "", "2026-09-04"),
    "Rocco Forte Hotels": ("Hotel Manager", "Rome", "€70,000", "2026-08-28"),
    "Mandarin Oriental": ("Director of Operations", "London",
                          "Competitive", "2026-08-24"),
    "Highgate": ("Area General Manager", "New York", "$120,000", "2026-08-20"),
    "The Peninsula": ("Front Office Manager", "Paris", "", "2026-08-18"),
    "Bob W": ("Head of Property", "Berlin", "", "2026-08-15"),
    "Grosvenor": ("Development Manager", "London", "£80,000", "2026-08-02"),
}


def _posting(conn, company):
    """Insert the posting behind a fixture opportunity, and return its row id.

    Returns None for a company with no posting, so `create_opportunity` gets
    job_id=None and the row renders with empty optional columns — which is a
    real state and needs to look right too.
    """
    facts = FIXTURE_POSTINGS.get(company)
    if facts is None:
        return None
    title, location, salary, posted = facts
    cur = conn.execute(
        """INSERT INTO jobs(provider, provider_job_id, title, company,
                            locations_json, description_text, posted_at,
                            salary, url)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        ("theirstack", f"fixture-{company}", title, company,
         json.dumps([location]), "", posted, salary,
         f"https://example.invalid/jobs/{company.lower().replace(' ', '-')}"))
    return cur.lastrowid


def _opp(conn, company, **kw):
    """create_opportunity, with the posting linked when there is one."""
    return create_opportunity(conn, company, job_id=_posting(conn, company), **kw)


def build_board():
    """The board, populated across every stage. Returns the window."""


    conn = db.connect(":memory:")
    db.migrate(conn)

    a = _opp(conn, "Round Hill Capital", stage=Stage.IDENTIFIED)
    b = _opp(conn, "Landmark Venues", stage=Stage.IDENTIFIED)
    c = _opp(conn, "Rocco Forte Hotels", stage=Stage.CONTACTED)
    d = _opp(conn, "Mandarin Oriental", stage=Stage.IN_DIALOGUE)
    e = _opp(conn, "Highgate", stage=Stage.PHONE_INTERVIEW)
    f = _opp(conn, "The Peninsula", stage=Stage.IN_PERSON_INTERVIEW)
    g = _opp(conn, "Bob W", stage=Stage.ON_HOLD)
    h = _opp(conn, "Grosvenor", stage=Stage.LOST)
    i = _opp(conn, "Marriott Feasibility", stage=Stage.WON)
    poc = _opp(conn, "Amir Mossanen (Truist)",
                             stage=Stage.CONTACTED, category=JobCategory.MUTUAL_POC)

    record_outbound(conn, str(c), Channel.EMAIL, TUE)
    record_outbound(conn, str(d), Channel.EMAIL, date(2026, 9, 1))
    add_task(conn, str(d), "Second dual touch", date(2026, 9, 15))
    add_task(conn, str(e), "Prepare for the call", date(2026, 9, 10))

    # One parity defect and one bounce, so the audit banner has something real.
    conn.execute("UPDATE opportunities SET status_mirror='open' WHERE id=?", (f,))
    cid = conn.execute("INSERT INTO contacts(opportunity_id, name, email, created_at)"
                       " VALUES(?, 'Paul', 'paul@travelfusion.com', 'x')",
                       (b,)).lastrowid
    record_bounce(conn, str(b), cid, TUE)
    conn.execute("UPDATE opportunities SET stage=1, status_mirror='waiting' WHERE id=?", (b,))
    conn.commit()

    win = BoardWindow()
    rows, findings = board_rows(conn, today=TUE)
    win.load(rows, findings)
    render_hidden(win, STORE_SIZE)

    # Select the bounced opportunity, so the screenshot shows the audit doing its
    # job rather than whatever row Qt happened to land on.
    # clearSelection first: setCurrentItem moves the CURRENT item but does not
    # clear a selection Qt already made, so two rows end up highlighted.
    win.tree.clearSelection()
    for i in range(win.tree.topLevelItemCount()):
        group = win.tree.topLevelItem(i)
        for j in range(group.childCount()):
            if group.child(j).text(0) == "Landmark Venues":
                win.tree.setCurrentItem(group.child(j))
                group.child(j).setSelected(True)
    for _ in range(8):
        QApplication.instance().processEvents()
    return win


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = build_board()
    out = Path(__file__).resolve().parents[1] / "docs" / "board-window.png"
    win.grab().save(str(out))
    print("wrote", out)
