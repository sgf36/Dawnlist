"""Render the review window to a PNG so a human can LOOK at it.

A passing smoke test proves the window constructed. It does not prove the
funnel bar is readable, the tabs are labelled, or the gold warning is visible.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n  # noqa: E402
from app.ui.review import ReviewRow, ReviewWindow  # noqa: E402

ROWS = [
    ReviewRow("1", "Director of Asset Management", "Round Hill Capital",
              "London", "https://ats.example/1",
              "Leading the asset management of a pan-European operational real "
              "estate portfolio. Candidates will have experience across hotel "
              "and living sectors.", bucket="strong",
              reason="Operational real estate, explicitly welcomes hospitality background"),
    ReviewRow("2", "General Manager Events", "Landmark Venues", "London",
              "https://ats.example/2",
              "Running 147 boxes and 350+ events a year at a landmark venue.",
              bucket="strong",
              reason="Tagged Entertainment Providers, but the substance is premium hospitality"),
    ReviewRow("3", "Senior Project Manager", "Kier", "Reading",
              "https://ats.example/3",
              "Hotel development programme, RIBA stages 3-6.",
              bucket="possible",
              reason="Tagged Construction; description is hotel development"),
    ReviewRow("4", "Head of Revenue", "Bob W", "Remote",
              "https://ats.example/4", "Revenue management across 20 properties.",
              bucket="judgement-call",
              reason="Revenue focus is adjacent to the brief",
              downgrade_reason="the quoted disqualifying line does not appear in "
                               "the description - treating this rejection as unverified"),
    ReviewRow("5", "Asset Manager", "Aviva", "London", "https://ats.example/5",
              "Must have a minimum of 10 years' experience in real estate.",
              bucket="rejected", reason="10 year floor, stated",
              disqualifying_quote="minimum of 10 years' experience in real estate"),
    ReviewRow("6", "Analyst, Portfolio", "Legal & General", "London",
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
]

COUNTS = {"swept": 1143, "deduped": 1088, "gated_out": 213,
          "screened_likely": 118, "screened_out": 757, "assessed": 112,
          "left_unread": 6}

app = QApplication(sys.argv)
w = ReviewWindow()
w.load(ROWS, COUNTS, incomplete_note="context exhausted")
w.resize(1180, 760)
w.show()
# Select the first shortlist row so the detail pane renders real content -
# an empty pane in a screenshot proves nothing about the pane.
docs = Path(__file__).resolve().parents[1] / "docs"

w.shortlist.setCurrentItem(w.shortlist.topLevelItem(0))
w.tabs.setCurrentIndex(0)
for _ in range(8):
    app.processEvents()
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
rtl.resize(1180, 760)
rtl.show()
rtl.tabs.setCurrentIndex(0)
rtl.shortlist.setCurrentItem(rtl.shortlist.topLevelItem(0))
for _ in range(8):
    app.processEvents()
rtl.grab().save(str(docs / "review-window-ar.png"))
i18n.set_locale("en")
print("wrote three to", docs)
