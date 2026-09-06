"""The board screen — every opportunity you are pursuing, ordered by stage.

Three things here are product decisions rather than layout:

  * **Grouped and ordered by Stage, never by status.** The status vocabulary's
    own order is scrambled relative to the pipeline, so no status-sorted board
    is coherent. Stage is the truth; the status shown beside it is a derived
    mirror.

  * **On Hold is its own group, and it is not greyed out like the closed ones.**
    Paused is not dead: it carries no cadence but still gets checked for a
    reply, and a board that buries it re-creates the failure where a paused
    thread silently becomes a forgotten one.

  * **The audit is on screen, not in a log.** Parity drift, bounce corrections
    and duplicate open tasks appear as a banner with counts. A defect nobody
    sees is a defect nobody fixes, and every one of these classes shipped in
    the production system before it was surfaced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from app.core.tracker import Stage
from app.i18n import tr
from app.ui.review import CREAM, GOLD, GOLD_DEEP, INK, TEAL, TEAL_LIFTED

#: Stages that read as finished, and are dimmed accordingly. On Hold is NOT
#: one of them.
CLOSED_STAGES = {Stage.WON, Stage.LOST}

BOARD_STYLESHEET = f"""
QFrame#auditBanner {{
    background: {GOLD_DEEP};
    border-radius: 6px;
}}
QFrame#auditBanner QLabel {{
    background: transparent;
    color: {CREAM};
    font-weight: 600;
}}
QFrame#auditBannerClean {{
    background: {TEAL};
    border-radius: 6px;
}}
QFrame#auditBannerClean QLabel {{
    background: transparent;
    color: {CREAM};
}}
QTreeWidget#boardTree {{
    border: 1px solid #d8d4cc;
    border-radius: 6px;
    outline: none;
}}
QTreeWidget#boardTree::item {{
    padding: 7px 6px;
}}
QTreeWidget#boardTree::item:selected {{
    background: {TEAL};
    color: {CREAM};
}}
QHeaderView::section {{
    background: #f4f1ea;
    color: #45505a;
    padding: 8px 6px;
    border: none;
    border-bottom: 1px solid #d8d4cc;
    font-weight: 600;
}}
QPushButton#boardAction {{
    min-height: 32px;
    padding: 7px 16px;
    border-radius: 6px;
    border: 1px solid #cfcabf;
    background: #ffffff;
    color: {INK};
}}
QPushButton#boardAction:hover {{ background: #f4f1ea; }}
"""


@dataclass
class BoardRow:
    """One opportunity, flattened for display."""
    opportunity_id: str
    company: str
    stage: Stage
    status: str
    next_step_on: date | None = None
    next_step_channels: str = ""
    open_task: str = ""
    #: Set when the stored mirror disagrees with the Stage.
    parity_defect: str = ""
    #: Set when a bounce means the STAGE is wrong, not the status.
    bounce_defect: str = ""

    @property
    def has_defect(self) -> bool:
        return bool(self.parity_defect or self.bounce_defect)


class AuditBanner(QFrame):
    """What the one-pass audit found. Always shown, including when clean."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(16, 10, 16, 10)
        self._layout.setSpacing(18)
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._layout.addWidget(self._label, 1)
        self.set_findings({})

    def set_findings(self, findings: dict[str, list]) -> None:
        parity = len(findings.get("parity_defects", []))
        bounces = len(findings.get("bounce_corrections", []))
        dupes = len(findings.get("duplicate_open_children", []))
        scanned = len(findings.get("scanned", []))

        if not (parity or bounces or dupes):
            self.setObjectName("auditBannerClean")
            self._label.setText(
                f"{scanned} opportunities checked — no drift found")
        else:
            self.setObjectName("auditBanner")
            parts = []
            if bounces:
                # Listed first: the Stage is wrong, which is the worse defect.
                parts.append(f"{bounces} bounced — stage says contacted, "
                             f"the address is dead")
            if parity:
                parts.append(f"{parity} status out of step with its stage")
            if dupes:
                parts.append(f"{dupes} with more than one open task")
            self._label.setText(f"{scanned} checked — " + "; ".join(parts))
        # Re-polish so the object-name swap actually repaints.
        self.style().unpolish(self)
        self.style().polish(self)


class BoardWindow(QWidget):
    """The board. Emits intent; it never writes to the database itself."""

    repair_requested = Signal(str)        # opportunity_id — fix the MIRROR
    bounce_repair_requested = Signal(str)  # opportunity_id — fix the STAGE
    opportunity_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("board.title"))
        self._rows: dict[str, BoardRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        self.audit = AuditBanner()
        outer.addWidget(self.audit)

        self.tree = QTreeWidget()
        self.tree.setObjectName("boardTree")
        self.tree.setHeaderLabels([
            tr("board.col.company"), tr("board.col.status"),
            tr("board.col.next_step"), tr("board.col.open_task")])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 190)
        self.tree.header().setStretchLastSection(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        outer.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_repair = QPushButton(tr("board.repair_mirror"))
        self.btn_repair_bounce = QPushButton(tr("board.repair_stage"))
        for b in (self.btn_repair, self.btn_repair_bounce):
            b.setObjectName("boardAction")
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        outer.addLayout(actions)

        self.setStyleSheet(BOARD_STYLESHEET)

        self.tree.currentItemChanged.connect(self._on_select)
        self.btn_repair.clicked.connect(self._emit_repair)
        self.btn_repair_bounce.clicked.connect(self._emit_bounce_repair)

    # -- population --------------------------------------------------------
    def load(self, rows: list[BoardRow], findings: dict[str, list]) -> None:
        self._rows = {r.opportunity_id: r for r in rows}
        self.tree.clear()
        self.audit.set_findings(findings)

        by_stage: dict[Stage, list[BoardRow]] = {}
        for row in rows:
            by_stage.setdefault(row.stage, []).append(row)

        # Stage order, not status order, and every stage that has rows —
        # including the closed ones, so nothing is audited out of view.
        for stage in sorted(by_stage, key=lambda s: s.value):
            group_rows = sorted(by_stage[stage], key=lambda r: r.company.casefold())
            group = QTreeWidgetItem([f"{stage.label} ({len(group_rows)})", "", "", ""])
            bold = QFont()
            bold.setBold(True)
            group.setFont(0, bold)
            group.setFirstColumnSpanned(True)
            # A group header carries no opportunity, so selecting it can do
            # nothing — but a selectable row that does nothing reads as a
            # broken click. Make it a heading, not a target.
            group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
            if stage in CLOSED_STAGES:
                group.setForeground(0, QColor("#8b9199"))
            elif stage.is_paused:
                # Paused, not dead — it keeps the live colour.
                group.setForeground(0, QColor(GOLD_DEEP))
            else:
                group.setForeground(0, QColor(TEAL))
            self.tree.addTopLevelItem(group)

            for row in group_rows:
                due = row.next_step_on.isoformat() if row.next_step_on else ""
                if due and row.next_step_channels:
                    due = f"{due} · {row.next_step_channels}"
                item = QTreeWidgetItem([row.company, row.status, due,
                                        row.open_task])
                item.setData(0, Qt.UserRole, row.opportunity_id)
                if row.bounce_defect:
                    item.setForeground(1, QColor(GOLD_DEEP))
                    item.setToolTip(1, row.bounce_defect)
                elif row.parity_defect:
                    item.setForeground(1, QColor(GOLD_DEEP))
                    item.setToolTip(1, row.parity_defect)
                if stage in CLOSED_STAGES:
                    item.setForeground(0, QColor("#8b9199"))
                group.addChild(item)
            group.setExpanded(True)

    # -- interaction -------------------------------------------------------
    def _current(self) -> BoardRow | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return self._rows.get(item.data(0, Qt.UserRole))

    def _on_select(self, *_):
        row = self._current()
        self.btn_repair.setEnabled(bool(row and row.parity_defect))
        self.btn_repair_bounce.setEnabled(bool(row and row.bounce_defect))
        if row:
            self.opportunity_selected.emit(row.opportunity_id)

    def _emit_repair(self):
        row = self._current()
        if row and row.parity_defect:
            self.repair_requested.emit(row.opportunity_id)

    def _emit_bounce_repair(self):
        row = self._current()
        if row and row.bounce_defect:
            self.bounce_repair_requested.emit(row.opportunity_id)
