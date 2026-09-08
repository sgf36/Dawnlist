"""Render the kill-family panel: one proposal, one armed.

Both states in one image, because the difference between them is the whole
point — a proposal screens nothing until the user arms it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.rules import KillFamily  # noqa: E402
from app.ui.settings import FamiliesPanel  # noqa: E402

FAMILIES = [
    KillFamily(name="Bramwell Construction", employers=("Bramwell Construction",),
               kill_titles=("engineer", "site"),
               saves_titles=("asset management", "strategy", "feasibility"),
               precedents=(("Bramwell Construction", "Site Engineer"),
                           ("Bramwell Construction", "Senior Site Engineer, Highways")),
               adopted=False),
    KillFamily(name="Ellerby Venues", employers=("Ellerby Venues",),
               kill_titles=("catering", "banqueting"),
               saves_titles=("commercial", "strategy", "revenue"),
               precedents=(("Ellerby Venues", "Head of Catering"),
                           ("Ellerby Venues", "Banqueting Operations Manager")),
               adopted=True),
]


def build_families():
    """The panel with one proposal and one armed family. Returns it."""
    panel = FamiliesPanel(loader=lambda: FAMILIES,
                          adopter=lambda name, on: None,
                          refresher=lambda: 0)
    panel.listing.setCurrentRow(0)
    return panel


if __name__ == "__main__":
    app = QApplication(sys.argv)
    panel = build_families()
    panel.setAttribute(Qt.WA_DontShowOnScreen, True)
    panel.resize(1366, 460)
    panel.show()
    for _ in range(8):
        app.processEvents()
    out = Path(__file__).resolve().parents[1] / "docs" / "kill-families.png"
    panel.grab().save(str(out))
    print("wrote", out)
