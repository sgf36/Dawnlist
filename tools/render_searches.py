"""Render the Searches panel to a PNG for the Store screenshot set.

The 1.1.6 feature set lives here: CSV import/export, description keywords,
search type selectors, last-run dates, test connection, and criteria guide
export. A screenshot that shows none of these is a listing for the version
before this one.

Fixtures deliberately show every state the panel can reach:

  * ON and OFF searches (the switch exists for a reason)
  * Title, Description and Both search types ([T], [D], [T+D])
  * Description keywords (the bracket syntax)
  * Last-run timestamps on some, absent on others (a newly added search)
  * A populated location scope (city + country)

The full SettingsWindow is rendered with the SearchesPanel injected — the user
opens Settings and scrolls to their searches, so the screenshot should show
what they actually see, not a detached widget.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tools.fixture_i18n import t  # noqa: E402

FIXTURE_SEARCHES = [
    {
        "label": "Hotel Asset Management",
        "titles": ["Hotel Asset Management", "Asset Manager Hotels"],
        "description_keywords": ["hospitality", "portfolio"],
        "enabled": True,
        "search_type": "both",
        "last_run": datetime(2026, 9, 16, 6, 1).isoformat(),
    },
    {
        "label": "General Manager",
        "titles": ["General Manager", "Hotel General Manager"],
        "description_keywords": [],
        "enabled": True,
        "search_type": "title",
        "last_run": datetime(2026, 9, 16, 6, 1).isoformat(),
    },
    {
        "label": "Director of Operations",
        "titles": ["Director of Operations", "Operations Director"],
        "description_keywords": ["hotel", "hospitality", "leisure"],
        "enabled": True,
        "search_type": "description",
        "last_run": datetime(2026, 9, 15, 6, 1).isoformat(),
    },
    {
        "label": "Head of Revenue",
        "titles": ["Head of Revenue", "Revenue Director"],
        "description_keywords": [],
        "enabled": False,
        "search_type": "title",
        "last_run": datetime(2026, 9, 14, 6, 1).isoformat(),
    },
    {
        "label": "Real Estate Investment",
        "titles": ["Real Estate Investment", "Investment Manager"],
        "description_keywords": ["operational", "living"],
        "enabled": True,
        "search_type": "both",
        "last_run": None,
    },
]

STORE_SIZE = (1366, 768)


def render_hidden(widget, size):
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.resize(*size)
    widget.show()
    return widget


def build_searches(*, size=None):
    """The Settings window, scrolled to show the searches panel."""
    from app.ui.settings import SearchesPanel, SettingsWindow

    fixture = list(FIXTURE_SEARCHES)
    # Translate the fixture labels and titles through fixture_i18n so
    # localised renders are not half English.
    translated = []
    for row in fixture:
        translated.append({
            **row,
            "label": t(row["label"]),
            "titles": [t(x) for x in row["titles"]],
            "description_keywords": [t(k) for k in row["description_keywords"]],
        })

    panel = SearchesPanel(
        detail_loader=lambda: translated,
        saver=lambda label, titles, dk=None: None,
        forgetter=lambda label: None,
        enabler=lambda label, on: None,
        where_loader=lambda: "London, GB",
        where_saver=lambda text: None,
        guide_exporter=lambda: None,
        guide_importer=lambda path: None,
        connection_tester=lambda: None,
    )

    win = SettingsWindow(variant="store_iap", searches=panel)
    render_hidden(win, size or STORE_SIZE)

    # Scroll so the searches panel is prominent — past the API key panel.
    # ensureWidgetVisible scrolls just enough that the widget's top appears;
    # we want it near the top of the viewport. Then nudge down a little more
    # so the bottom buttons (test connection) are fully visible.
    win.scroll.ensureWidgetVisible(panel, 0, 0)

    for _ in range(12):
        QApplication.instance().processEvents()

    # Push the viewport down a bit further so CSV/guide/test-connection
    # buttons are all visible rather than clipped at the foot.
    sb = win.scroll.verticalScrollBar()
    sb.setValue(sb.value() + 60)

    for _ in range(4):
        QApplication.instance().processEvents()
    return win


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = build_searches()
    out = Path(__file__).resolve().parents[1] / "docs" / "settings-searches.png"
    win.grab().save(str(out))
    print("wrote", out)
