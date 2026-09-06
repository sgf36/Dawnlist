"""Render the interview step so a human can LOOK at it.

The content below is not invented for the picture: it is what the real
`render_factsheet` produced from a two-CV corpus on a live model, trimmed to
fit. Mocking up prettier text would hide the thing worth checking, which is how
a long factsheet and a short brief sit next to each other.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.onboarding import InterviewPage  # noqa: E402

STORE_SIZE = (1366, 768)

FACTSHEET = """# Background factsheet

## Acme Hotels
**Asset Manager** · 2019 to 2023
*Also appears as:* Asset Management Lead (cv-tailored.docx)

- Reviewed a 14-property operational portfolio and identified
  GBP 4.2m in cost opportunities across procurement and energy.
  *identified GBP 4.2m* — this verb may not be strengthened
- Supported the disposal of three assets alongside the
  transactions team.
  *supported three assets* — this verb may not be strengthened
- Coordinated a working group of five across finance and
  operations.
  *led five (working group members, not direct reports)*

## Grand Riverside Hotel
**Front Office Supervisor** · 2016 to 2019

## Must never be claimed

*As valuable as the claims themselves: these look supportable
and are not.*

- Do not claim the GBP 4.2m figure was 'saved', 'delivered' or
  'closed' — the corpus states it was 'identified' as a cost
  opportunity only, not realised.
- Do not claim ownership or leadership of the asset disposals —
  the corpus states the role 'supported' the disposal alongside
  the transactions team.
- Do not claim 'managed a team of five' or any direct-report
  headcount — 'coordinated a working group of five across
  finance and operations' is cross-functional coordination, not
  line management.
"""

BRIEF = """# Fit brief

## In scope

- Operational real estate and asset management where the assets
  are hotels, serviced apartments or similar operating property.
- Strategy and commercial roles inside a hotel group, an owner
  or an operator.
- General management and front-of-house leadership at a single
  property of scale.

## Out of scope

- Construction and main-contractor project management.
- Pure investment or fund roles with no operating exposure.
- Roles stating a hard requirement of ten years or more.

## Location

London and the South East. Remote considered where the
employer is UK-based.
"""

QUESTIONS = [
    "No information on employment after the Acme Hotels role ended in 2023 — "
    "confirm current status before drafting outreach that assumes present "
    "employment.",
    "Unclear whether the 'working group of five' had any formal authority or "
    "deliverables — clarify scope before describing it in outreach.",
    "Two CV variants give different titles for the same Acme Hotels role — "
    "confirm which, if either, is authoritative.",
]


def render_hidden(widget, size):
    """Paint the widget without mapping it to the display — see
    render_onboarding.py for why the offscreen platform plugin is not used."""
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.resize(*size)
    widget.show()
    return widget


app = QApplication(sys.argv)
page = InterviewPage(drafter=lambda corpus: (FACTSHEET, BRIEF, QUESTIONS))
render_hidden(page, STORE_SIZE)
page.run_draft(["cv-2023.docx", "cv-tailored.docx"])
for _ in range(8):
    app.processEvents()

out = Path(__file__).resolve().parents[1] / "docs" / "interview-step.png"
page.grab().save(str(out))
print("wrote", out)
