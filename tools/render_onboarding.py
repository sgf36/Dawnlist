"""Render the calibration gate so a human can LOOK at it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.onboarding.calibration import CalibrationItem  # noqa: E402
from tools.fixture_i18n import t as fx  # noqa: E402
from app.ui.onboarding import CalibrationPage  # noqa: E402

SAMPLE = [
    ("Director of Asset Management", "Oakmere Capital", "possible",
     "operational real estate, but the brief asks for hospitality"),
    ("General Manager Events", "Ellerby Venues", "rejected",
     "tagged Entertainment Providers, outside the brief"),
    ("Senior Project Manager", "Bramwell Construction", "rejected", "tagged Construction"),
    ("Head of Revenue", "Loftly", "strong", "revenue leadership in hospitality"),
    ("Asset Manager", "Thornfield", "rejected", "10 year floor, stated"),
    ("Analyst, Portfolio Oversight", "Wardley & Vance", "possible",
     "description could not be fetched in full"),
    ("Cluster Revenue Manager", "Calderwood Hotels", "strong", "multi-property revenue"),
    ("Front Office Manager", "Grand Hotel", "rejected", "below the band"),
    ("Head of Commercial Strategy", "Castellan Hotels", "strong",
     "strategy at a luxury operator"),
    ("Operations Analyst", "Thatcham Taverns", "rejected", "pub estate operations"),
]

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


def build_calibration():
    """The calibration gate, part-way through. Returns the page."""

    page = CalibrationPage()
    # `fx`, not `t` — the comprehension already binds `t` to the title. The
    # company is deliberately untranslated: an invented proper noun.
    page.load([CalibrationItem(job_key=str(i), title=fx(t), company=c,
                               description="d", app_verdict=v,
                               app_reason=fx(r))
               for i, (t, c, v, r) in enumerate(SAMPLE)])

    # Part-way through: seven decided, one disagreement still missing its sentence.
    for i in range(7):
        for btn in page._widgets[i].group.buttons():
            if btn.property("verdict") == SAMPLE[i][2]:
                btn.setChecked(True)
    for btn in page._widgets[1].group.buttons():
        if btn.property("verdict") == "strong":
            btn.setChecked(True)

    render_hidden(page, STORE_SIZE)
    for _ in range(8):
        QApplication.instance().processEvents()
    return page


if __name__ == "__main__":
    app = QApplication(sys.argv)
    page = build_calibration()
    out = Path(__file__).resolve().parents[1] / "docs" / "calibration-gate.png"
    page.grab().save(str(out))
    print("wrote", out)
