"""Render the screening-rules panel, including a refused term.

The refusal is the state worth looking at: it is the only place the admission
guard's finding reaches a person, and "invalid term" in red would throw it away.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.rules import RuleConflict, RuleConflictError, RuleTable  # noqa: E402
from app.ui.settings import RulesPanel  # noqa: E402
from tools.fixture_i18n import t  # noqa: E402

TABLE = RuleTable(
    unsupported_titles=[t(_x) for _x in ["night auditor", "housekeeping", "kitchen porter",
                        "commis chef", "site engineer"]],
    strong_terms=[t(_x) for _x in ["asset management", "asset strategy", "portfolio strategy"]],
    contextual_terms=[t(_x) for _x in ["hospitality", "hotel", "leisure", "portfolio"]],
    known_employers=["Northaven Hotels", "Oakmere Capital", "Loftly"],
)


def refuse(field, term):
    raise RuleConflictError([RuleConflict(
        term=t("operations"), field="unsupported_titles",
        pursued_title=t("Head of Operations"), company="Oakmere Capital")])


def build_rules():
    """The screening rules, showing a refused term. Returns the panel."""

    panel = RulesPanel(loader=lambda: TABLE, saver=refuse,
                       forgetter=lambda f, t: None)
    panel.setAttribute(Qt.WA_DontShowOnScreen, True)
    panel.resize(1366, 560)
    panel.show()

    panel._fields["unsupported_titles"].setText("operations")
    panel.add("unsupported_titles")
    for _ in range(8):
        QApplication.instance().processEvents()
    return panel


if __name__ == "__main__":
    app = QApplication(sys.argv)
    panel = build_rules()
    out = Path(__file__).resolve().parents[1] / "docs" / "screening-rules.png"
    panel.grab().save(str(out))
    print("wrote", out)
