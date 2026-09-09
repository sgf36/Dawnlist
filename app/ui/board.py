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
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit,
                               QMenu, QMessageBox,
                               QPushButton,
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

    # --- posting facts, for the optional columns ---------------------------
    # Empty is normal: an opportunity created by hand has no posting behind
    # it. An empty cell is honest; a placeholder would not be.
    job_title: str = ""
    location: str = ""
    salary: str = ""
    job_url: str = ""
    posted_at: date | None = None
    created_at: date | None = None
    #: The newest OUTBOUND touch. Evidence of what was sent, not a plan.
    last_outbound_on: date | None = None

    @property
    def has_defect(self) -> bool:
        return bool(self.parity_defect or self.bounce_defect)



def _next_step_text(row: "BoardRow") -> str:
    """Due date and the channels it is due on, in one cell.

    They belong together: a date with no channel does not say what to do, and
    a channel with no date does not say when.
    """
    due = row.next_step_on.isoformat() if row.next_step_on else ""
    if due and row.next_step_channels:
        return f"{due} · {row.next_step_channels}"
    return due



def _date(value) -> str:
    return value.isoformat() if value else ""


@dataclass(frozen=True)
class Column:
    """One board column, declared rather than hand-wired.

    Adding a column used to mean editing four places that had to agree — the
    header labels, three setColumnWidth calls and the QTreeWidgetItem
    constructor — and getting them out of step showed up as data under the
    wrong heading, which reads as a data bug rather than a layout one. Now a
    column is one entry here.
    """
    key: str
    label_key: str
    width: int
    #: Shown unless the user says otherwise. The default set is the one that
    #: answers "what do I do next", which is what the board is for; the rest
    #: are reference and are off until asked for, because a tracker with
    #: fourteen columns is read by nobody.
    default_visible: bool
    value: "Callable[[BoardRow], str]"


COLUMNS: tuple[Column, ...] = (
    Column("company", "board.col.company", 220, True, lambda r: r.company),
    # The single biggest omission before this: an opportunity is keyed on the
    # employer, so without this the board could not say which ROLE was being
    # pursued — the thing a job search is actually about.
    Column("role", "board.col.role", 220, True, lambda r: r.job_title),
    Column("status", "board.col.status", 150, True, lambda r: r.status),
    Column("last_out", "board.col.last_contact", 110, True,
           lambda r: _date(r.last_outbound_on)),
    Column("next_step", "board.col.next_step", 190, True, _next_step_text),
    Column("open_task", "board.col.open_task", 200, True, lambda r: r.open_task),
    Column("location", "board.col.location", 150, False, lambda r: r.location),
    Column("salary", "board.col.salary", 140, False, lambda r: r.salary),
    Column("posted", "board.col.posted", 110, False, lambda r: _date(r.posted_at)),
    Column("first_tracked", "board.col.first_tracked", 110, False,
           lambda r: _date(r.created_at)),
    Column("url", "board.col.url", 240, False, lambda r: r.job_url),
)

#: Where the visible set is remembered, as comma-separated keys.
COLUMN_SETTING = "board_visible_columns"


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
    sent_recorded = Signal(str)           # opportunity_id — a message went out
    task_added = Signal(str, str)         # (opportunity_id, title)
    opportunity_selected = Signal(str)
    #: The visible column set changed, as comma-separated keys. Emitted rather
    #: than written here: the board renders, the adapter persists, and a widget
    #: that opened its own database connection would be the exception that
    #: makes the rule useless.
    columns_changed = Signal(str)
    #: Write the application for this opportunity — a tailored CV and a
    #: covering letter, and an interview brief when asked for.
    #:
    #: Emitted, not done here. This is the one board action that SPENDS the
    #: user's own tokens, and a widget that could spend money by itself is
    #: exactly the boundary the rest of this class exists to keep.
    application_requested = Signal(str, bool)   # (opportunity_id, want_brief)
    #: The employer answered. (opportunity_id, positive, stage) — `stage` is
    #: the Stage value to advance to and is meaningless when `positive` is
    #: false, because a negative determination has exactly one destination.
    #:
    #: This is the only event that moves an opportunity past Contacted. A send
    #: never implies a reply, so without it every pursuit sits at Contacted for
    #: ever and the cadence chases a thread that ended weeks ago.
    determination_recorded = Signal(str, bool, int)

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
        self._visible: list[str] = [c.key for c in COLUMNS if c.default_visible]
        self._apply_columns()
        self.tree.header().setStretchLastSection(True)
        # Right-click the header to choose columns. Qt's own convention, so it
        # needs no button and no explaining, and it is where a user who wants
        # more columns will look first.
        self.tree.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._column_menu)
        # Alternating row colours OFF. With setFirstColumnSpanned group headers
        # they made a non-selected row paint as if selected — the widget state
        # said one row was selected while two were painted. The stylesheet's
        # per-item bottom border already separates rows, so nothing is lost.
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        # The ::item:selected stylesheet rule paints the item, not the strip
        # beyond the last column, so the default highlight shows through there
        # and a selected row comes out two colours. Set the palette too.
        palette = self.tree.palette()
        palette.setColor(QPalette.Highlight, QColor(TEAL))
        palette.setColor(QPalette.HighlightedText, QColor(CREAM))
        palette.setColor(QPalette.Inactive, QPalette.Highlight, QColor(TEAL))
        palette.setColor(QPalette.Inactive, QPalette.HighlightedText, QColor(CREAM))
        self.tree.setPalette(palette)
        outer.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        # Dawnlist never sends, so it cannot observe that a message went out —
        # only the user can say so. Without this the cadence sits at rung zero
        # for ever: the same first-contact letter is redrafted every Tuesday
        # and no follow-up is ever scheduled.
        # The "Open task" column had no way to be filled: `add_task` existed
        # and nothing called it, so the column was permanently blank. Tasks are
        # for what the cadence cannot know — "prepare for the call" — so the
        # user types them.
        self.field_task = QLineEdit()
        self.field_task.setObjectName("taskField")
        self.field_task.setPlaceholderText(tr("board.task_placeholder"))
        self.field_task.setEnabled(False)
        self.btn_task = QPushButton(tr("board.add_task"))
        self.btn_sent = QPushButton(tr("board.mark_sent"))
        self.btn_repair = QPushButton(tr("board.repair_mirror"))
        self.btn_repair_bounce = QPushButton(tr("board.repair_stage"))
        self.btn_apply = QPushButton(tr("board.write_application"))
        self.btn_brief = QPushButton(tr("board.interview_brief"))
        # An employer's answer. The stage is chosen rather than inferred: a
        # reply and an interview invitation are both "they answered" and land
        # two rungs apart, and guessing between them corrupts the pipeline
        # read exactly as inferring a stage from a send would.
        self.combo_stage = QComboBox()
        self.combo_stage.setObjectName("stageCombo")
        for stage in (Stage.IN_DIALOGUE, Stage.PHONE_INTERVIEW,
                      Stage.IN_PERSON_INTERVIEW, Stage.OFFER, Stage.WON):
            self.combo_stage.addItem(stage.label, int(stage))
        self.combo_stage.setEnabled(False)
        self.btn_reply = QPushButton(tr("board.record_reply"))
        self.btn_no_offer = QPushButton(tr("board.record_no_offer"))
        actions.addWidget(self.field_task, 1)
        actions.addWidget(self.combo_stage)
        for b in (self.btn_task, self.btn_sent, self.btn_reply,
                  self.btn_no_offer, self.btn_apply,
                  self.btn_brief, self.btn_repair, self.btn_repair_bounce):
            b.setObjectName("boardAction")
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        outer.addLayout(actions)

        self.setStyleSheet(BOARD_STYLESHEET)

        self.tree.currentItemChanged.connect(self._on_select)
        self.btn_task.clicked.connect(self._emit_task)
        self.field_task.returnPressed.connect(self._emit_task)
        self.btn_sent.clicked.connect(self._emit_sent)
        self.btn_repair.clicked.connect(self._emit_repair)
        self.btn_repair_bounce.clicked.connect(self._emit_bounce_repair)
        self.btn_apply.clicked.connect(
            lambda: self._emit_application(want_brief=False))
        self.btn_brief.clicked.connect(
            lambda: self._emit_application(want_brief=True))
        self.btn_reply.clicked.connect(
            lambda: self._emit_determination(positive=True))
        self.btn_no_offer.clicked.connect(
            lambda: self._emit_determination(positive=False))

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
            group = QTreeWidgetItem(
                    [f"{stage.label} ({len(group_rows)})"]
                    + [""] * (len(self._shown()) - 1))
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
                item = QTreeWidgetItem([col.value(row) for col in self._shown()])
                item.setData(0, Qt.UserRole, row.opportunity_id)
                # The defect marker follows the STATUS column wherever the
                # user has put it, and is simply not drawn if they have hidden
                # it. Hard-coding column 1 painted the warning onto whatever
                # happened to be second.
                status_at = self._index_of("status")
                if status_at is not None:
                    defect = row.bounce_defect or row.parity_defect
                    if defect:
                        item.setForeground(status_at, QColor(GOLD_DEEP))
                        item.setToolTip(status_at, defect)
                if stage in CLOSED_STAGES:
                    item.setForeground(0, QColor("#8b9199"))
                group.addChild(item)
            group.setExpanded(True)

    # -- columns -----------------------------------------------------------
    def _shown(self) -> list[Column]:
        """Visible columns, in the catalogue's order.

        Order comes from COLUMNS rather than from the saved list so that a
        column added in a later release appears in its intended place instead
        of being appended to whatever the user last saved.
        """
        return [c for c in COLUMNS if c.key in self._visible]

    def _index_of(self, key: str) -> int | None:
        for i, col in enumerate(self._shown()):
            if col.key == key:
                return i
        return None

    def _apply_columns(self) -> None:
        shown = self._shown()
        self.tree.setColumnCount(len(shown))
        self.tree.setHeaderLabels([tr(c.label_key) for c in shown])
        for i, col in enumerate(shown):
            self.tree.setColumnWidth(i, col.width)

    def set_visible_columns(self, keys) -> None:
        """Apply a saved or chosen set, ignoring keys this build knows nothing
        about and never ending up with nothing.

        A saved set from a later version can name a column this build does not
        have; silently dropping it is right. An empty result is not — it would
        render a board with no columns and no way to get them back — so it
        falls back to the defaults.
        """
        wanted = [k for k in keys if any(c.key == k for c in COLUMNS)]
        self._visible = wanted or [c.key for c in COLUMNS if c.default_visible]
        self._apply_columns()
        self.set_rows(list(self._rows.values()))

    def visible_columns(self) -> list[str]:
        return list(self._visible)

    def _column_menu(self, point) -> None:
        menu = QMenu(self)
        for col in COLUMNS:
            action = menu.addAction(tr(col.label_key))
            action.setCheckable(True)
            action.setChecked(col.key in self._visible)
            # The first column is what carries the row's identity and the
            # click target, so it cannot be hidden.
            if col.key == COLUMNS[0].key:
                action.setEnabled(False)
            action.toggled.connect(
                lambda on, key=col.key: self._toggle_column(key, on))
        menu.exec(self.tree.header().mapToGlobal(point))

    def _toggle_column(self, key: str, on: bool) -> None:
        keys = [c.key for c in COLUMNS
                if (c.key in self._visible or c.key == key) and
                not (c.key == key and not on)]
        self.set_visible_columns(keys)
        self.columns_changed.emit(",".join(self._visible))

    # -- interaction -------------------------------------------------------
    def _current(self) -> BoardRow | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return self._rows.get(item.data(0, Qt.UserRole))

    def _on_select(self, *_):
        row = self._current()
        # Only a live stage carries a cadence. Recording a send against a Won,
        # Lost or On Hold record would schedule a chase on a closed pursuit.
        self.btn_sent.setEnabled(bool(row and row.stage.is_live))
        # At most one open task per opportunity — the schema enforces it, so
        # offering the field when one is already open would only produce a
        # constraint error the user cannot act on.
        can_add = bool(row and row.stage.is_live and not row.open_task)
        self.btn_task.setEnabled(can_add)
        self.field_task.setEnabled(can_add)
        self.btn_repair.setEnabled(bool(row and row.parity_defect))
        self.btn_repair_bounce.setEnabled(bool(row and row.bounce_defect))
        # Only a live pursuit. Writing a covering letter for something already
        # Won or Lost spends the user's own tokens on a document nobody will
        # send, and a live button implies pressing it is worth doing.
        live = bool(row and row.stage.is_live)
        self.btn_apply.setEnabled(live)
        self.btn_brief.setEnabled(live)
        # An answer can only arrive on a pursuit that is still open. Offering
        # it on a Lost record invites re-closing something already closed, and
        # on a Won one it would walk an accepted offer backwards.
        self.combo_stage.setEnabled(live)
        self.btn_reply.setEnabled(live)
        self.btn_no_offer.setEnabled(live)
        if row:
            self.opportunity_selected.emit(row.opportunity_id)

    def _emit_task(self):
        row = self._current()
        title = self.field_task.text().strip()
        if row and title and row.stage.is_live and not row.open_task:
            self.task_added.emit(row.opportunity_id, title)
            self.field_task.clear()

    def _emit_sent(self):
        row = self._current()
        if row and row.stage.is_live:
            self.sent_recorded.emit(row.opportunity_id)

    def _emit_application(self, *, want_brief: bool):
        """Two buttons rather than one with a modifier key.

        The brief is for an interview nobody has offered yet and it spends the
        user's own tokens, so it must be asked for — but hiding that behind
        shift-click makes a paid action undiscoverable, which is worse than
        one more button on a row that already has four.
        """
        row = self._current()
        if row is None:
            return
        self.application_requested.emit(row.opportunity_id, want_brief)

    def _emit_determination(self, *, positive: bool):
        row = self._current()
        if row is None or not row.stage.is_live:
            return
        stage = int(self.combo_stage.currentData() or int(Stage.IN_DIALOGUE))
        self.determination_recorded.emit(row.opportunity_id, positive, stage)

    def confirm_determination(self, lines: list[str]) -> bool:
        """Show what is about to be written and wait for a yes.

        A negative determination does not touch one field: the parent goes
        Lost and EVERY subtask underneath it is rendered `no offer`. That
        cascade is correct — the whole tree really did end — and it is far too
        much to happen because a button was next to the one being aimed at.

        Returned as a bool so the adapter, which owns the database, decides
        whether to commit. The window still writes nothing itself.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("board.confirm_title"))
        box.setText(tr("board.confirm_body"))
        box.setInformativeText("\n".join(lines))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Yes

    def _emit_repair(self):
        row = self._current()
        if row and row.parity_defect:
            self.repair_requested.emit(row.opportunity_id)

    def _emit_bounce_repair(self):
        row = self._current()
        if row and row.bounce_defect:
            self.bounce_repair_requested.emit(row.opportunity_id)
