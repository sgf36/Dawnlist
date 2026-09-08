"""Render the review window to a PNG so a human can LOOK at it.

A passing smoke test proves the window constructed. It does not prove the
funnel bar is readable, the tabs are labelled, or the gold warning is visible.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.ui.review import ReviewRow, ReviewWindow  # noqa: E402

ROWS = [
    ReviewRow("1", "Director of Asset Management", "Oakmere Capital",
              "London", "https://ats.example/1",
              "Leading the asset management of a pan-European operational real "
              "estate portfolio. Candidates will have experience across hotel "
              "and living sectors.", bucket="strong",
              reason="Operational real estate, explicitly welcomes hospitality background"),
    ReviewRow("2", "General Manager Events", "Ellerby Venues", "London",
              "https://ats.example/2",
              "Running 147 boxes and 350+ events a year at a landmark venue. "
              "You will own the commercial performance of the events business, "
              "from pricing and yield through to the client relationships that "
              "drive repeat bookings, working alongside the venue's operations "
              "and catering teams.\n\nThe role reports to the Managing Director "
              "and carries full P&L responsibility for the events division. "
              "Candidates will have run a comparable operation at scale, and "
              "will be comfortable moving between commercial strategy and the "
              "detail of delivery on a matchday.",
              bucket="strong",
              reason="Tagged Entertainment Providers, but the substance is premium hospitality"),
    ReviewRow("3", "Senior Project Manager", "Bramwell Construction", "Reading",
              "https://ats.example/3",
              "Hotel development programme, RIBA stages 3-6.",
              bucket="possible",
              reason="Tagged Construction; description is hotel development"),
    ReviewRow("4", "Head of Revenue", "Loftly", "Remote",
              "https://ats.example/4", "Revenue management across 20 properties.",
              bucket="judgement-call",
              reason="Revenue focus is adjacent to the brief",
              downgrade_reason="the quoted disqualifying line does not appear in "
                               "the description - treating this rejection as unverified"),
    ReviewRow("5", "Asset Manager", "Thornfield", "London", "https://ats.example/5",
              "Must have a minimum of 10 years' experience in real estate.",
              bucket="rejected", reason="10 year floor, stated",
              disqualifying_quote="minimum of 10 years' experience in real estate"),
    ReviewRow("6", "Analyst, Portfolio", "Wardley & Vance", "London",
              "https://ats.example/6", "Description could not be retrieved.",
              bucket="possible", reason="description unavailable",
              requirement_checked=False),
    ReviewRow("7", "Assistant Front Office Manager", "Grand Hotel", "London",
              "https://ats.example/7", "Front office leadership role.",
              bucket="screened-out",
              screen_reason="unsupported title term 'Office Manager' - matched "
                            "INSIDE a longer role name; review before trusting "
                            "this kill",
              contained=True),
    ReviewRow("8", "Kitchen Porter", "Acme Hotels", "London", "",
              "Washing up.", bucket="screened-out",
              screen_reason="unsupported title term 'Kitchen Porter'"),
    ReviewRow("9", "Head of Commercial Strategy", "Castellan Hotels",
              "London", "https://ats.example/9",
              "Owning commercial strategy across a luxury European portfolio.",
              bucket="strong",
              reason="Strategy at a luxury operator, squarely in the brief"),
    ReviewRow("10", "Cluster Revenue Manager", "Calderwood Hotels", "Manchester",
              "https://ats.example/10",
              "Revenue management across six properties in the North West.",
              bucket="possible",
              reason="Multi-property revenue, a step below the stated band"),
    ReviewRow("11", "Asset Management Associate", "Thornfield Investors",
              "London", "https://ats.example/11",
              "Supporting the operational real estate team.",
              bucket="possible",
              reason="Operational real estate, but associate-level"),
    ReviewRow("12", "Director of Operations", "Ellerby Trust", "Bristol",
              "https://ats.example/12",
              "CEO-reporting strategic mandate across a heritage portfolio.",
              bucket="strong",
              reason="Generic title over a strategic mandate; substance decides"),
    ReviewRow("13", "Development Manager", "Copperfield Inns", "Dunstable",
              "https://ats.example/13",
              "Hotel development pipeline across the UK estate.",
              bucket="possible",
              reason="Development rather than asset management, adjacent"),
    # Enough rows that the table FILLS the pane. A screenshot of a
    # half-empty list reads as an app with nothing in it, and the shortlist is
    # the one screen a buyer judges the product by.
    ReviewRow("14", "Head of Asset Management, UK & Ireland", "Halverton Estates",
              "London", "https://ats.example/14",
              "Asset management across a mixed hotel and leisure portfolio.",
              bucket="strong",
              reason="Hotel asset management at portfolio level, in the band"),
    ReviewRow("15", "Director, Hotel Investment", "Brackmoor Capital", "London",
              "https://ats.example/15",
              "Underwriting and asset strategy for hotel investments.",
              bucket="strong",
              reason="Investment with an operating-asset mandate"),
    ReviewRow("16", "Commercial Director", "Ardmore Hotel Group", "London",
              "https://ats.example/16",
              "Commercial performance across the UK estate.",
              bucket="possible",
              reason="Commercial rather than asset-side, but an operator"),
    ReviewRow("17", "Senior Asset Manager", "Pinfold Capital", "London",
              "https://ats.example/17",
              "Operational real estate asset management.",
              bucket="strong",
              reason="Squarely in the brief; operational real estate"),
    ReviewRow("18", "Portfolio Manager, Living", "Latchford Living", "London",
              "https://ats.example/18",
              "Asset management across a build-to-rent portfolio.",
              bucket="possible",
              reason="Living rather than hospitality, adjacent sector"),
    ReviewRow("19", "Head of Strategy, Hotels", "Northaven Hotels", "Windsor",
              "https://ats.example/19",
              "Group strategy for the hotel estate.",
              bucket="strong",
              reason="Hotel strategy at group level, in the brief"),
    ReviewRow("20", "Investment Manager", "Ashcombe Ridge Partners", "London",
              "https://ats.example/20",
              "Travel and leisure investments across Europe.",
              bucket="strong",
              reason="Named target employer; travel and leisure mandate"),
]

COUNTS = {"swept": 1143, "deduped": 1088, "gated_out": 213,
          "screened_likely": 118, "screened_out": 757, "assessed": 112,
          "left_unread": 6}

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


def build_review():
    """The review window with the shortlist selected. Returns it."""

    w = ReviewWindow()
    w.load(ROWS, COUNTS, incomplete_note="context exhausted")
    render_hidden(w, STORE_SIZE)
    # Select the first shortlist row so the detail pane renders real content -
    # an empty pane in a screenshot proves nothing about the pane.

    w.tabs.setCurrentIndex(0)
    # Select by TITLE, not by position: the sort order changes whenever the sample
    # does, and row 0 is not reliably the row worth showing.
    for _i in range(w.shortlist.topLevelItemCount()):
        if w.shortlist.topLevelItem(_i).text(0) == "General Manager Events":
            w.shortlist.setCurrentItem(w.shortlist.topLevelItem(_i))
            break
    for _ in range(8):
        QApplication.instance().processEvents()
    return w


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = build_review()
    docs = Path(__file__).resolve().parents[1] / "docs"
    w.grab().save(str(docs / "review-window.png"))
    
    # The "Needs review" tab: a kill term that matched inside a longer role name.
    w.tabs.setCurrentIndex(3)
    w.contained.setCurrentItem(w.contained.topLevelItem(0))
    for _ in range(8):
        app.processEvents()
    w.grab().save(str(docs / "review-window-contained.png"))
    # Arabic: the whole window must mirror, not just the strings.
    i18n.set_locale("ar")
    i18n.clear_cache()
    rtl = ReviewWindow()
    rtl.load(ROWS, COUNTS, incomplete_note="نفد السياق")
    render_hidden(rtl, STORE_SIZE)
    rtl.tabs.setCurrentIndex(0)
    rtl.shortlist.setCurrentItem(rtl.shortlist.topLevelItem(0))
    for _ in range(8):
        app.processEvents()
    rtl.grab().save(str(docs / "review-window-ar.png"))
    i18n.set_locale("en")
    print("wrote three to", docs)
