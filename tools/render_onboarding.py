"""Render the calibration gate so a human can LOOK at it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.onboarding.calibration import CalibrationItem  # noqa: E402
from app.ui.onboarding import CalibrationPage  # noqa: E402

SAMPLE = [
    ("Director of Asset Management", "Round Hill Capital", "possible",
     "operational real estate, but the brief asks for hospitality"),
    ("General Manager Events", "Landmark Venues", "rejected",
     "tagged Entertainment Providers, outside the brief"),
    ("Senior Project Manager", "Kier", "rejected", "tagged Construction"),
    ("Head of Revenue", "Bob W", "strong", "revenue leadership in hospitality"),
    ("Asset Manager", "Aviva", "rejected", "10 year floor, stated"),
    ("Analyst, Portfolio Oversight", "Legal & General", "possible",
     "description could not be fetched in full"),
    ("Cluster Revenue Manager", "Accor", "strong", "multi-property revenue"),
    ("Front Office Manager", "Grand Hotel", "rejected", "below the band"),
    ("Head of Commercial Strategy", "Rocco Forte", "strong",
     "strategy at a luxury operator"),
    ("Operations Analyst", "Greene King", "rejected", "pub estate operations"),
]

app = QApplication(sys.argv)
page = CalibrationPage()
page.load([CalibrationItem(job_key=str(i), title=t, company=c, description="d",
                          app_verdict=v, app_reason=r)
           for i, (t, c, v, r) in enumerate(SAMPLE)])

# Part-way through: seven decided, one disagreement still missing its sentence.
for i in range(7):
    for btn in page._widgets[i].group.buttons():
        if btn.property("verdict") == SAMPLE[i][2]:
            btn.setChecked(True)
for btn in page._widgets[1].group.buttons():
    if btn.property("verdict") == "strong":
        btn.setChecked(True)

page.resize(860, 780)
page.show()
for _ in range(8):
    app.processEvents()
out = Path(__file__).resolve().parents[1] / "docs" / "calibration-gate.png"
page.grab().save(str(out))
print("wrote", out)
