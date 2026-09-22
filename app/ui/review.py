"""The morning review screen.

Two things about this window are product decisions, not layout choices:

  * **The funnel bar is always visible.** Invariant 3: a count is never shown
    without what it excludes. The user sees swept -> deduped -> gated ->
    screened -> assessed on every run, so a narrowing can never happen quietly.

  * **The rejects are a tab, not a deletion.** The `unlikely` pile and the
    contained-match pile are both browsable. An over-aggressive rule shows up
    here as rows the user disagrees with, rather than as months of silence.

Decisions are the user's: Pursue / Reject / Later. Nothing is auto-decided, and
a rejection is permanent (spec 4), which is why the button says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QIcon,
                           QKeySequence, QPainter, QPixmap, QShortcut)
from app.i18n import is_rtl, tr
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QSplitter,
                               QTabWidget, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

# Brand tokens. Teal ground, gold figure, cream line — never recolour the line
# to gold, and never put the product mark on an ink tile.
TEAL = "#1E4B45"
TEAL_LIFTED = "#1F5C54"
GOLD = "#C98A3F"
GOLD_DEEP = "#B07A2E"
CREAM = "#F0ECE4"
INK = "#16212A"

# Per-tab accent colours — within the teal/gold brand family.
TAB_COLOURS = {
    0: TEAL,            # Shortlist — primary positive
    1: "#2A6B5E",       # Pursuing — brighter teal (active engagement)
    2: GOLD_DEEP,       # Rejected — user said no
    3: "#7a7267",       # Lost — muted warm grey (employer said no)
    4: "#8b9199",       # Screened out — cool grey (automated)
    5: "#8B6914",       # Needs review — dark gold (needs attention)
}

# Row-level accent colours for bucket decoration.
BUCKET_COLOURS = {
    "strong": TEAL,
    "possible": "#2A6B5E",
    "judgement-call": "#5C7A6A",
    "rejected": GOLD_DEEP,
    "declined": GOLD_DEEP,
    "lost": "#7a7267",
    "screened-out": "#8b9199",
    "pursued": "#2A6B5E",
}

#: The warning chip must never push the funnel counts off the bar.
WARNING_MAX_WIDTH = 420

BUCKET_ORDER = {"strong": 0, "possible": 1, "judgement-call": 2, "rejected": 3}


def _dot_icon(colour: str, size: int = 10) -> QIcon:
    """A small filled circle in the given colour, for use as a tab icon."""
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(colour))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)

_MD_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')
_MD_BOLD = re.compile(r'\*\*(.+?)\*\*|__(.+?)__')
_MD_ITALIC = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)')


def _md_to_html(text: str) -> str:
    """Lightweight markdown-to-HTML for job descriptions in QTextBrowser."""
    html_lines: list[str] = []
    in_list = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("# ", "## ", "### ")):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = len(stripped.split(" ", 1)[0])
            tag = f"h{min(level + 1, 4)}"
            html_lines.append(f"<{tag}>{stripped.lstrip('# ').strip()}</{tag}>")
            continue
        is_bullet = stripped.startswith(("- ", "* ", "• "))
        if is_bullet:
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{stripped[2:].strip()}</li>")
            continue
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        if not stripped:
            html_lines.append("<br>")
        else:
            html_lines.append(f"{stripped}<br>")
    if in_list:
        html_lines.append("</ul>")
    result = "".join(html_lines)
    result = re.sub(r'(</(?:ul|h[2-4])>)(?:<br>)+', r'\1', result)
    result = re.sub(r'(?:<br>)+(<(?:ul|h[2-4])[ >])', r'\1', result)
    result = _MD_LINK.sub(r'<a href="\2">\1</a>', result)
    result = _MD_BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", result)
    result = _MD_ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", result)
    return result


@dataclass
class ReviewRow:
    """One row on the review screen, independent of where it came from."""
    job_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    bucket: str = "possible"
    reason: str = ""
    disqualifying_quote: str | None = None
    requirement_checked: bool = True
    downgrade_reason: str | None = None
    screen_reason: str = ""
    contained: bool = False
    #: "also posted as X at Y" — the near-duplicate this row was flagged
    #: against. Never merged (spec 6.6): two postings that look like one may be
    #: two real vacancies, and merging them loses one.
    near_duplicate: str = ""
    #: Recovered from an earlier run rather than produced by the current one
    #: (spec 6.5). The board reads one run, so an undecided posting from
    #: yesterday would otherwise disappear the moment a new run finishes —
    #: which reads as the app having lost it, because it had.
    carried_forward: bool = False


#: One stylesheet, every selector scoped by object name or class.
#:
#: Qt stylesheets CASCADE to child widgets. Setting `background` on a container
#: silently repaints every label inside it, and `border-radius` turns each one
#: into its own little box with no padding — which is exactly how the funnel
#: bar ended up as a row of chips with the text jammed against the edges.
#: Scope every rule, and give anything with a background explicit padding.
STYLESHEET = f"""
QFrame#funnelBar {{
    background: {TEAL};
    border-radius: 8px;
}}
/* Transparent, so the panel above is the only thing drawing a background. */
QFrame#funnelBar QWidget {{
    background: transparent;
    color: {CREAM};
}}
QFrame#funnelBar QLabel#funnelWarning {{
    background: {GOLD_DEEP};
    color: {CREAM};
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
}}

/* Rows need vertical padding or the text sits taller than the row it is in. */
QTreeWidget {{
    border: 1px solid #d8d4cc;
    border-radius: 6px;
    outline: none;
}}
QTreeWidget::item {{
    padding: 7px 6px;
    border-bottom: 1px solid #efece6;
}}
QTreeWidget::item:selected {{
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

/* All three buttons styled together: styling ONE of them drops Qt's native
   metrics for that one only, which is what made the heights disagree. */
QPushButton#decisionButton {{
    min-height: 34px;
    padding: 8px 20px;
    border-radius: 6px;
    border: 1px solid #cfcabf;
    background: #ffffff;
    color: {INK};
}}
QPushButton#decisionButton:hover {{ background: #f4f1ea; }}
QPushButton#decisionButtonPrimary {{
    min-height: 34px;
    padding: 8px 20px;
    border-radius: 6px;
    border: 1px solid {TEAL};
    background: {TEAL};
    color: {CREAM};
    font-weight: 600;
}}
QPushButton#decisionButtonPrimary:hover {{ background: {TEAL_LIFTED}; }}
QPushButton#decisionButtonDanger {{
    min-height: 34px;
    padding: 8px 20px;
    border-radius: 6px;
    border: 1px solid {GOLD_DEEP};
    background: {GOLD_DEEP};
    color: {CREAM};
    font-weight: 600;
}}
QPushButton#decisionButtonDanger:hover {{ background: {GOLD}; }}

QTextBrowser#detailPane {{
    border: 1px solid #d8d4cc;
    border-radius: 6px;
    padding: 4px 10px;
}}
QTabBar::tab {{
    padding: 8px 14px;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
    color: #6b7280;
}}
QTabBar::tab:selected {{
    background: {CREAM};
    border: 1px solid #d8d4cc;
    border-bottom: 2px solid {TEAL};
    color: {TEAL};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{
    background: #f9f7f3;
    color: {INK};
}}
"""

#: The run controls, appended rather than written into the block above so the
#: two can change independently. The status box has a background, so it has
#: explicit padding for the reason given on the block above.
STYLESHEET += f"""
QLabel#runStatus {{
    background: #f7efe2;
    border: 1px solid {GOLD_DEEP};
    border-radius: 6px;
    padding: 8px 12px;
    color: {INK};
}}
QLabel#runNote {{ color: #45505a; padding: 2px 2px; }}
QLabel#lastRun {{ color: #45505a; }}
QPushButton#runNowButton {{
    min-height: 30px;
    padding: 6px 18px;
    border-radius: 6px;
    border: 1px solid {TEAL};
    background: {TEAL};
    color: {CREAM};
    font-weight: 600;
}}
QPushButton#runNowButton:hover {{ background: {TEAL_LIFTED}; }}
"""


class FunnelBar(QFrame):
    """swept -> deduped -> gated -> screened -> assessed, always on screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("funnelBar")
        self._layout = QHBoxLayout(self)
        # Generous inner margins: this panel has a background, so its contents
        # must not touch its edges.
        self._layout.setContentsMargins(18, 12, 18, 12)
        self._layout.setSpacing(26)
        self._labels: dict[str, QWidget] = {}

    @staticmethod
    def _stat(value: int, caption: str) -> QWidget:
        """One stat, as two PLAIN-TEXT labels in a column.

        Deliberately not one rich-text label: an HTML `line-height` renders
        taller than the sizeHint Qt reports for it, so the caption clipped
        against the panel edge and no minimum-height guess fixed it reliably.
        Two real labels let Qt measure both lines exactly, and the column sizes
        itself correctly in every locale — including scripts far taller than
        Latin, which is where a hand-tuned height would break first.
        """
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        number = QLabel(str(value))
        nf = QFont()
        nf.setPointSizeF(QFont().pointSizeF() + 4)
        nf.setBold(True)
        number.setFont(nf)
        number.setAlignment(Qt.AlignCenter)

        text = QLabel(caption)
        cf = QFont()
        cf.setPointSizeF(max(QFont().pointSizeF() - 1.5, 7.0))
        text.setFont(cf)
        text.setAlignment(Qt.AlignCenter)

        col.addWidget(number)
        col.addWidget(text)
        return box

    def _warn_chip(self, text: str) -> None:
        """One gold chip on the bar. Elided by hand — QLabel clips instead."""
        warn = QLabel()
        warn.setObjectName("funnelWarning")
        warn.setToolTip(text)
        warn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        warn.setMaximumWidth(WARNING_MAX_WIDTH)
        metrics = QFontMetrics(warn.font())
        warn.setText(metrics.elidedText(
            text, Qt.ElideRight, WARNING_MAX_WIDTH - 28))
        self._layout.addWidget(warn)

    def set_counts(self, counts: dict[str, int], *, incomplete_note: str = "",
                   notes: list[str] | None = None) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._labels.clear()

        stages = [("swept", tr("funnel.swept")), ("deduped", tr("funnel.deduped")),
                  ("gated_out", tr("funnel.gated")),
                  ("screened_likely", tr("funnel.screened_in")),
                  ("screened_out", tr("funnel.screened_out")),
                  ("assessed", tr("funnel.assessed"))]
        for key, label in stages:
            if key not in counts:
                continue
            w = self._stat(counts[key], label)
            self._layout.addWidget(w)
            self._labels[key] = w

        self._layout.addStretch(1)

        # An incomplete run says so, in the bar, in gold. Never filed as normal.
        left = counts.get("left_unread", 0)
        if left or incomplete_note:
            if left:
                text = "⚠ " + tr("funnel.left_unread", count=left)
            else:
                text = "⚠ " + tr("funnel.incomplete")
            if incomplete_note:
                text += f" — {incomplete_note}"
            self._warn_chip(text)

        # Anything else the run wants to say about itself — today, that the
        # screen's reach moved sharply since last time (spec 5.4). Its own
        # chip: folding it into the incomplete note would label a complete run
        # incomplete, and the two are not the same problem.
        for note in (notes or []):
            self._warn_chip("⚠ " + note)


_REJECT_REASONS = [
    "reject.wrong_role",
    "reject.wrong_company",
    "reject.wrong_location",
    "reject.not_relevant",
]


class RejectReasonDialog(QDialog):
    """One-click reason picker shown when the user rejects a posting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("reject.title"))
        self.setMinimumWidth(360)
        self._reason = ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("reject.hint")))

        chips = QHBoxLayout()
        for key in _REJECT_REASONS:
            btn = QPushButton(tr(key))
            btn.clicked.connect(lambda _=False, k=key: self._pick(tr(k)))
            chips.addWidget(btn)
        layout.addLayout(chips)

        other_row = QHBoxLayout()
        other_row.addWidget(QLabel(tr("reject.other")))
        self._text = QLineEdit()
        self._text.setPlaceholderText(tr("reject.placeholder"))
        self._text.returnPressed.connect(self._submit_text)
        other_row.addWidget(self._text)
        layout.addLayout(other_row)

        buttons = QHBoxLayout()
        skip = QPushButton(tr("reject.skip"))
        skip.clicked.connect(lambda: self._pick(""))
        submit = QPushButton(tr("reject.submit"))
        submit.setDefault(True)
        submit.clicked.connect(self._submit_text)
        buttons.addStretch()
        buttons.addWidget(skip)
        buttons.addWidget(submit)
        layout.addLayout(buttons)

    def _pick(self, reason: str):
        self._reason = reason
        self.accept()

    def _submit_text(self):
        self._reason = self._text.text().strip()
        self.accept()

    @staticmethod
    def ask(parent=None) -> str | None:
        dlg = RejectReasonDialog(parent)
        if dlg.exec() == QDialog.Accepted:
            return dlg._reason
        return None


class ReviewWindow(QMainWindow):
    decided = Signal(str, str, str)     # (job_id, decision, note)
    settings_requested = Signal()
    #: Open the board. Until 2026-09-13 the board could be reached ONLY with
    #: the `--board` command-line flag, so on every shipped build a posting
    #: marked Pursue went somewhere the user could never open again.
    board_requested = Signal()
    #: Back to the first screen of setup. Asked for after a user was left with
    #: no way to redo setup short of deleting the app's data by hand.
    restart_setup_requested = Signal()
    #: Open setup at its searches step, to calibrate. Without this a user whose
    #: setup finished uncalibrated -- normal before subscribing -- was refused
    #: by every daily run with nothing on screen that could fix it.
    calibrate_requested = Signal()
    #: The same three things the setup wizard offers, on the window a user
    #: spends every morning in. Language was previously unreachable anywhere.
    language_chosen = Signal(str)
    subscribe_requested = Signal()
    restore_requested = Signal()
    #: Job-alert emails the user dropped on the window. The listing promises
    #: "add job-alert emails yourself for anything the feeds miss", and the
    #: parser for them was complete and reachable from nowhere.
    alerts_dropped = Signal(list)
    #: Run now was pressed. The window never decides whether a run may start;
    #: whoever owns the schedule does, and tells it through `set_run_state`.
    run_now_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("app.title"))
        # RTL locales flip the whole window, same as the other apps.
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl()
            else Qt.LayoutDirection.LeftToRight)
        self.resize(1180, 760)
        self._rows: dict[str, ReviewRow] = {}

        # The only route to Settings. Without it someone who has finished
        # onboarding can never change their API key, and on a bring-your-own-key
        # app a rotated, revoked or mistyped key then leaves the whole thing
        # inert with nothing on screen that could fix it.
        # Only .eml and .mbox are accepted, and the check happens on the drag
        # rather than the drop: a window that lights up for any file and then
        # refuses it has already told the user the wrong thing.
        self.setAcceptDrops(True)

        self.act_settings = QAction(tr("menu.settings"), self)
        self.act_settings.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.act_settings.triggered.connect(self.settings_requested)
        app_menu = self.menuBar().addMenu(tr("menu.app"))
        # Language, subscription and Restore, from the same builder the setup
        # wizard uses — so the two cannot offer different things. Settings is
        # added by `populate`; the QAction above is kept because macOS moves
        # anything with PreferencesRole into the application menu itself.
        from app.ui.quickmenu import populate
        from app.core.build_variant import variant
        populate(app_menu, build=variant(),
                 on_language=self.language_chosen.emit,
                 on_subscribe=self.subscribe_requested.emit,
                 on_restore=self.restore_requested.emit,
                 on_settings=self.settings_requested.emit,
                 settings_action=self.act_settings,
                 on_restart_setup=self.restart_setup_requested.emit)
        self.act_board = QAction(tr("menu.board"), self)
        self.act_board.triggered.connect(self.board_requested)
        app_menu.insertAction(app_menu.actions()[0], self.act_board)

        #: The factsheet and CV text, joined. Set by whoever opens the window.
        #:
        #: Empty by default, and the gap section stays hidden while it is —
        #: before onboarding every requirement is unsupported, and a list of
        #: forty missing things teaches the reader to skip the section rather
        #: than telling them anything.
        self.evidence = ""

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        self.funnel = FunnelBar()
        outer.addWidget(self.funnel)

        # PIPELINE-P6: a failed or partial run says so beside its counts. The
        # counts of a failed run are mostly zero, and without this the window
        # showed them over yesterday's carried-forward postings as though the
        # morning had simply been quiet.
        self.run_status = QLabel()
        self.run_status.setObjectName("runStatus")
        self.run_status.setWordWrap(True)
        self.run_status.setTextFormat(Qt.PlainText)
        self.run_status.hide()
        outer.addWidget(self.run_status)

        run_row = QHBoxLayout()
        run_row.setSpacing(10)
        self.last_run = QLabel()
        self.last_run.setObjectName("lastRun")
        self.run_busy = QProgressBar()
        # A range of nothing is Qt's indeterminate bar: a run reports no
        # fraction done, and a bar that guessed one would stall at 90%.
        self.run_busy.setRange(0, 0)
        self.run_busy.setTextVisible(False)
        self.run_busy.setMaximumWidth(140)
        self.run_busy.hide()
        self.run_progress = QLabel(tr("run.running"))
        self.run_progress.setObjectName("lastRun")
        self.run_progress.hide()
        self.btn_run_now = QPushButton(tr("run.now"))
        self.btn_run_now.setObjectName("runNowButton")
        self.btn_run_now.setToolTip(tr("run.now_tip"))
        self.btn_run_now.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_run_now.hide()
        self.btn_run_now.setEnabled(False)
        self.btn_run_now.clicked.connect(self.run_now_requested)
        # VISIBLE, not only in the menu. On Windows the menu bar is a single
        # plain word that does not look like a menu, and a user on this screen
        # could not see how to reach Settings (reported 2026-09-13). The board
        # is the other half of the app and had no way in at all.
        self.btn_calibrate = QPushButton(tr("run.calibrate_now"))
        self.btn_calibrate.setObjectName("runNowButton")
        self.btn_calibrate.clicked.connect(self.calibrate_requested)
        self.btn_calibrate.hide()
        self.calibration_note = QLabel()
        self.calibration_note.setObjectName("runStatus")
        self.calibration_note.setWordWrap(True)
        self.calibration_note.setTextFormat(Qt.PlainText)
        self.calibration_note.hide()
        self.btn_export = QPushButton(tr("menu.export"))
        self.btn_export.setObjectName("secondaryButton")
        self.btn_export.clicked.connect(self._export_csv)
        self.btn_import = QPushButton(tr("menu.import"))
        self.btn_import.setObjectName("secondaryButton")
        self.btn_import.clicked.connect(self._import_csv)
        self.btn_board = QPushButton(tr("menu.board"))
        self.btn_board.setObjectName("secondaryButton")
        self.btn_board.clicked.connect(self.board_requested)
        self.btn_settings = QPushButton(tr("menu.settings"))
        self.btn_settings.setObjectName("secondaryButton")
        self.btn_settings.clicked.connect(self.settings_requested)
        run_row.addWidget(self.last_run)
        run_row.addWidget(self.run_busy)
        run_row.addWidget(self.run_progress)
        run_row.addStretch(1)
        run_row.addWidget(self.btn_calibrate)
        run_row.addWidget(self.btn_export)
        run_row.addWidget(self.btn_import)
        run_row.addWidget(self.btn_board)
        run_row.addWidget(self.btn_settings)
        run_row.addWidget(self.btn_run_now)
        outer.addLayout(run_row)
        outer.addWidget(self.calibration_note)

        splitter = QSplitter(Qt.Horizontal)

        self.tabs = QTabWidget()
        self.shortlist = self._make_tree()
        self.pursuing = self._make_tree()
        self.rejected = self._make_tree()
        self.lost = self._make_tree()
        self.screened_out = self._make_tree()
        self.contained = self._make_tree()
        self.tabs.addTab(self.shortlist, tr("tab.shortlist"))
        self.tabs.addTab(self.pursuing, tr("tab.pursuing"))
        self.tabs.addTab(self.rejected, tr("tab.rejected"))
        self.tabs.addTab(self.lost, tr("tab.lost"))
        # spec 5.4: the unlikely pile is browsable, never erased.
        self.tabs.addTab(self.screened_out, tr("tab.screened_out"))
        self.tabs.addTab(self.contained, tr("tab.needs_review"))
        for idx, colour in TAB_COLOURS.items():
            self.tabs.setTabIcon(idx, _dot_icon(colour))
        splitter.addWidget(self.tabs)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        from app.ui import enable_touch_scroll
        enable_touch_scroll(self.detail)
        rl.addWidget(self.detail, 1)

        buttons = QHBoxLayout()
        self.btn_pursue = QPushButton(tr("btn.pursue"))
        self.btn_later = QPushButton(tr("btn.later"))
        self.btn_reject = QPushButton(tr("btn.reject"))
        self.btn_pursue.setObjectName("decisionButtonPrimary")
        self.btn_later.setObjectName("decisionButton")
        self.btn_reject.setObjectName("decisionButtonDanger")
        buttons.setSpacing(10)
        buttons.setContentsMargins(0, 10, 0, 0)
        for b in (self.btn_pursue, self.btn_later, self.btn_reject):
            # A translated label is often much longer than the English one, so
            # the button sizes to its text rather than to a fixed width.
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            buttons.addWidget(b)
        buttons.addStretch(1)
        rl.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setSizes([620, 560])
        outer.addWidget(splitter, 1)

        self.detail.setObjectName("detailPane")
        self.setStyleSheet(STYLESHEET)
        self.setCentralWidget(root)

        for tree in (self.shortlist, self.pursuing, self.rejected,
                     self.lost, self.screened_out, self.contained):
            tree.currentItemChanged.connect(self._show_detail)
        # AND ON A TAB CHANGE, which nothing did.
        #
        # `_decide` reads `_current_row()`, which resolves against the CURRENT
        # TAB. `_show_detail` was wired only to selection changes inside a
        # tree, so switching tabs left the pane describing the posting from
        # the tab you just left while every button acted on the one selected
        # in the tab you arrived at.
        #
        # A person reads posting A and presses "Reject - permanently" on
        # posting B. Rejections have no expiry by design (spec 4), so that is
        # not a mistake the app lets them take back. Found because a store
        # screenshot showed a detail pane naming a different job from the row
        # highlighted beside it.
        self.tabs.currentChanged.connect(self._show_detail)
        self.btn_pursue.clicked.connect(lambda: self._decide("pursue"))
        self.btn_later.clicked.connect(lambda: self._decide("later"))
        self.btn_reject.clicked.connect(lambda: self._decide("reject"))

        # Keyboard shortcuts. Single letters are safe: no editable text field
        # exists on this screen, and the detail pane is read-only. The three
        # decision keys match the button initials in English; the number keys
        # work in every language and the tooltips announce them.
        QShortcut(QKeySequence("P"), self).activated.connect(
            lambda: self._decide("pursue"))
        QShortcut(QKeySequence("L"), self).activated.connect(
            lambda: self._decide("later"))
        QShortcut(QKeySequence("R"), self).activated.connect(
            lambda: self._decide("reject"))
        # Vim-style navigation: J/K move to next/previous posting.
        QShortcut(QKeySequence("J"), self).activated.connect(
            self._select_next)
        QShortcut(QKeySequence("K"), self).activated.connect(
            self._select_prev)
        # Tab switching: 1–6 jump to the six tabs.
        for i in range(6):
            QShortcut(QKeySequence(str(i + 1)), self).activated.connect(
                lambda idx=i: self.tabs.setCurrentIndex(idx))

        self.btn_pursue.setToolTip(tr("shortcut.pursue"))
        self.btn_later.setToolTip(tr("shortcut.later"))
        self.btn_reject.setToolTip(tr("shortcut.reject"))

    # -- construction ------------------------------------------------------
    @staticmethod
    def _make_tree() -> QTreeWidget:
        t = QTreeWidget()
        t.setHeaderLabels([tr("col.title"), tr("col.company"), tr("col.why")])
        # 215 elided the most important field in the window: real titles run to
        # "Head of Asset Management, UK & Ireland", and a truncated title is the
        # one thing a reader cannot skim past. Company is widened to match —
        # "KSL Capital Partners" did not fit either. Both stay user-resizable,
        # and both carry a tooltip, because no width fits every title.
        t.setColumnWidth(0, 310)
        t.setColumnWidth(1, 165)
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(True)
        # "Why" carries the verdict reason and the screen reason — the column
        # the user actually reads to decide. It must take the slack and stay
        # readable when the pane is narrow, so it stretches and the row tooltip
        # carries the full text.
        header = t.header()
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(90)
        t.setTextElideMode(Qt.ElideRight)
        t.setWordWrap(False)
        # Uniform row heights: without this Qt measures every row and a single
        # tall glyph (Arabic, Devanagari) stretches one row out of line.
        t.setUniformRowHeights(True)
        from app.ui import enable_touch_scroll
        enable_touch_scroll(t)
        return t

    def set_calibration_needed(self, needed: bool) -> None:
        """Say that the daily search cannot run until calibration, and offer it.

        A separate line from `run_status`, which reports what the last run did:
        this is about what no run can do yet, and one would overwrite the other.
        """
        self.calibration_note.setText(tr("run.calibration_needed") if needed else "")
        self.calibration_note.setVisible(needed)
        self.btn_calibrate.setVisible(needed)

    def load(self, rows: list[ReviewRow], counts: dict[str, int], *,
             incomplete_note: str = "",
             notes: list[str] | None = None) -> None:
        self._rows = {r.job_id: r for r in rows}
        for tree in (self.shortlist, self.pursuing, self.rejected,
                     self.lost, self.screened_out, self.contained):
            tree.clear()

        ordered = sorted(rows, key=lambda r: (BUCKET_ORDER.get(r.bucket, 9),
                                              r.company.casefold()))
        for r in ordered:
            if r.bucket == "pursued":
                target = self.pursuing
            elif r.bucket == "lost":
                target = self.lost
            elif r.bucket == "declined":
                target = self.rejected
            elif r.screen_reason and r.bucket == "screened-out":
                target = self.contained if r.contained else self.screened_out
            elif r.bucket == "rejected":
                target = self.rejected
            else:
                target = self.shortlist
            why = r.reason or r.screen_reason
            item = QTreeWidgetItem([r.title, r.company, why])
            item.setData(0, Qt.UserRole, r.job_id)
            # The elided column is still fully readable on hover.
            item.setToolTip(2, why)
            item.setToolTip(0, r.title)
            item.setToolTip(1, r.company)
            if r.bucket == "strong":
                f = QFont()
                f.setBold(True)
                item.setFont(0, f)
            if r.downgrade_reason or r.contained:
                item.setForeground(2, Qt.darkYellow)
            bucket_colour = BUCKET_COLOURS.get(r.bucket)
            if bucket_colour:
                item.setForeground(0, QColor(bucket_colour))
            target.addTopLevelItem(item)

        self.funnel.set_counts(counts, incomplete_note=incomplete_note,
                               notes=notes)
        for idx, (tree, key) in enumerate((
                (self.shortlist, "tab.shortlist"),
                (self.pursuing, "tab.pursuing"),
                (self.rejected, "tab.rejected"),
                (self.lost, "tab.lost"),
                (self.screened_out, "tab.screened_out"),
                (self.contained, "tab.needs_review"))):
            self.tabs.setTabText(idx, tr("tab.with_count", label=tr(key),
                                         count=tree.topLevelItemCount()))

    # -- the daily run -----------------------------------------------------
    def set_run_state(self, *, offered: bool, running: bool) -> None:
        """Show Run now exactly when it is offered and nothing is running.

        Hidden rather than only greyed out when it is not offered: a disabled
        button with no explanation reads as broken, and pressing it before the
        run time would spend the day's refreshes ahead of the scheduled run.
        """
        available = offered and not running
        self.btn_run_now.setVisible(available)
        self.btn_run_now.setEnabled(available)
        self.run_busy.setVisible(running)
        self.run_progress.setVisible(running)

    def set_last_run(self, text: str) -> None:
        self.last_run.setText(text)

    def set_run_status(self, text: str, *, problem: bool = True) -> None:
        self.run_status.setObjectName("runStatus" if problem else "runNote")
        self.run_status.style().unpolish(self.run_status)
        self.run_status.style().polish(self.run_status)
        self.run_status.setText(text)
        self.run_status.setVisible(bool(text))

    # -- interaction -------------------------------------------------------
    def _current_row(self) -> ReviewRow | None:
        tree = self.tabs.currentWidget()
        item = tree.currentItem() if isinstance(tree, QTreeWidget) else None
        if item is None:
            return None
        return self._rows.get(item.data(0, Qt.UserRole))

    def _select_next(self) -> None:
        """Move to the next posting in the current tab."""
        tree = self.tabs.currentWidget()
        if not isinstance(tree, QTreeWidget) or tree.topLevelItemCount() == 0:
            return
        current = tree.currentItem()
        if current is None:
            tree.setCurrentItem(tree.topLevelItem(0))
        else:
            idx = tree.indexOfTopLevelItem(current)
            nxt = idx + 1
            if nxt < tree.topLevelItemCount():
                tree.setCurrentItem(tree.topLevelItem(nxt))

    def _select_prev(self) -> None:
        """Move to the previous posting in the current tab."""
        tree = self.tabs.currentWidget()
        if not isinstance(tree, QTreeWidget) or tree.topLevelItemCount() == 0:
            return
        current = tree.currentItem()
        if current is None:
            tree.setCurrentItem(
                tree.topLevelItem(tree.topLevelItemCount() - 1))
        else:
            idx = tree.indexOfTopLevelItem(current)
            prev = idx - 1
            if prev >= 0:
                tree.setCurrentItem(tree.topLevelItem(prev))

    def _show_detail(self, *_):
        r = self._current_row()
        if r is None:
            self.detail.setHtml("")
            return
        parts = [
            f"<h2 style='color:{TEAL};margin-bottom:2px'>{r.title}</h2>",
            f"<div style='color:#555'>{r.company} — {r.location}</div>",
        ]
        if r.url:
            # ATS-canonical where the provider gave one: the link to apply through.
            parts.append(
                f"<p><a href='{r.url}'>{tr('detail.open_posting')}</a></p>")
        # THE LABEL, NEVER THE BUCKET VALUE. This rendered `r.bucket` raw, so
        # every language showed "strong" / "possible" / "rejected" in English.
        # Invisible in an English build, which is why it survived: "Verdict:
        # strong" reads as a sentence. In Japanese it reads "判定: strong".
        #
        # `onboarding.py` has always been careful about this — its
        # app_verdict_label() docstring says the app's verdict must never be
        # described in the user's own button vocabulary — and this screen
        # simply never used it. The keys live under `onboarding.app.*` because
        # that is where they already exist, translated into all fifty locales;
        # the namespace is historical, not meaningful.
        label = tr(f"onboarding.app.{r.bucket}") if r.bucket else ""
        verdict = f"{label} — {r.reason}" if r.reason else label
        parts.append(f"<p><b>{tr('detail.verdict')}</b> {verdict}</p>")
        if r.disqualifying_quote:
            parts.append(
                f"<blockquote style='border-left:3px solid {GOLD};padding-left:8px;"
                f"color:#333'>{r.disqualifying_quote}</blockquote>")
        if not r.requirement_checked:
            parts.append(
                f"<p style='color:#8a6d3b'><b>{tr('detail.not_checked_title')}</b> "
                f"— {tr('detail.not_checked_body')}</p>")
        if r.downgrade_reason:
            parts.append(
                f"<p style='color:#8a6d3b'><b>{tr('detail.downgraded')}</b> "
                f"{r.downgrade_reason}</p>")
        if r.contained:
            parts.append(
                f"<p style='color:#8a6d3b'><b>{tr('detail.contained_title')}</b> "
                f"— {tr('detail.contained_body')}</p>")
        if r.near_duplicate:
            # Shown, never merged (spec 6.6). Two postings that look like one
            # may be two real vacancies, and merging them loses one — so this
            # is a note for the reader, not a decision taken for them.
            parts.append(
                f"<p style='color:#8a6d3b'><b>{tr('detail.near_duplicate')}</b> "
                f"{r.near_duplicate}</p>")
        if r.screen_reason:
            parts.append("<p style='color:#666'><i>"
                         + tr("detail.screen", reason=r.screen_reason)
                         + "</i></p>")
        parts.extend(self._gap_html(r))
        parts.append("<hr>")
        parts.append(f"<div>{_md_to_html(r.description)}</div>")
        self.detail.setHtml("".join(parts))

    def _gap_html(self, r) -> list[str]:
        """What this posting states that the evidence does not support.

        Rendered here, in the pane where the pursue-or-reject decision is
        actually made, because that is the only moment it changes anything.
        It costs nothing to compute — no API call, no token — so it runs on
        every selection rather than behind a button nobody presses.

        Silent when there is no evidence yet. Before onboarding, EVERY
        requirement is unsupported, and a list of forty missing things is not
        a finding, it is noise that teaches the reader to skip the section.
        """
        if not self.evidence.strip() or not r.description.strip():
            return []

        from app.apply.keywords import analyse

        report = analyse(r.description, self.evidence)
        if report.is_empty:
            return []

        out = [f"<p style='margin-top:10px'><b>{tr('detail.gaps_title')}</b></p>"]
        missing = [q.phrase for q in report.missing_required]
        if missing:
            out.append(
                f"<p style='color:#8a6d3b'>{tr('detail.gaps_missing')} "
                + ", ".join(missing) + "</p>")
        else:
            out.append(f"<p style='color:#3d6b52'>{tr('detail.gaps_none')}</p>")

        # Preferences are shown separately and quietly. Presenting a
        # nice-to-have as though it were a bar is the false positive that
        # costs somebody an afternoon.
        preferred = [q.phrase for q in report.missing_preferred]
        if preferred:
            out.append(f"<p style='color:#666'><i>{tr('detail.gaps_preferred')} "
                       + ", ".join(preferred) + "</i></p>")
        return out

    # -- job-alert emails, dragged in --------------------------------------
    ALERT_SUFFIXES = {".eml", ".mbox"}

    def _alert_paths(self, mime) -> list:
        from pathlib import Path
        if not mime.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in mime.urls()
                if u.isLocalFile()
                and Path(u.toLocalFile()).suffix.lower() in self.ALERT_SUFFIXES]

    def dragEnterEvent(self, event):  # noqa: N802 - Qt naming
        if self._alert_paths(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        paths = self._alert_paths(event.mimeData())
        if paths:
            self.alerts_dropped.emit(paths)
            event.acceptProposedAction()

    def _decide(self, decision: str):
        tree = self.tabs.currentWidget()
        if not isinstance(tree, QTreeWidget):
            return
        item = tree.currentItem()
        if item is None:
            return
        job_id = item.data(0, Qt.UserRole)
        r = self._rows.get(job_id)
        if r is None:
            return

        note = ""
        if decision == "reject":
            reason = RejectReasonDialog.ask(self)
            if reason is None:
                return
            note = reason

        idx = tree.indexOfTopLevelItem(item)
        tree.takeTopLevelItem(idx)

        if decision == "reject":
            self.rejected.addTopLevelItem(item)
        elif decision == "pursue":
            self.pursuing.addTopLevelItem(item)
        elif decision == "later":
            pass

        self._refresh_tab_counts()
        self.decided.emit(r.job_id, decision, note)

        if tree.topLevelItemCount():
            nxt = min(idx, tree.topLevelItemCount() - 1)
            tree.setCurrentItem(tree.topLevelItem(nxt))
        else:
            self._show_detail()

    def _refresh_tab_counts(self):
        for idx, (tree, key) in enumerate((
                (self.shortlist, "tab.shortlist"),
                (self.pursuing, "tab.pursuing"),
                (self.rejected, "tab.rejected"),
                (self.lost, "tab.lost"),
                (self.screened_out, "tab.screened_out"),
                (self.contained, "tab.needs_review"))):
            self.tabs.setTabText(idx, tr("tab.with_count", label=tr(key),
                                         count=tree.topLevelItemCount()))

    # -- CSV export / import ------------------------------------------------
    _TAB_NAMES = {0: "Shortlist", 1: "Pursuing", 2: "Rejected",
                  3: "Lost", 4: "Screened out", 5: "Needs review"}

    def _all_rows_by_tab(self):
        """Yield (tab_name, ReviewRow) for every item across all tabs."""
        for idx, tree in enumerate((self.shortlist, self.pursuing,
                                    self.rejected, self.lost,
                                    self.screened_out, self.contained)):
            tab = self._TAB_NAMES.get(idx, "")
            for i in range(tree.topLevelItemCount()):
                job_id = tree.topLevelItem(i).data(0, Qt.UserRole)
                r = self._rows.get(job_id)
                if r:
                    yield tab, r

    def _export_csv(self):
        import csv, io
        from PySide6.QtCore import QStandardPaths
        docs = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation)
        default = f"{docs}/dawnlist_review.csv" if docs else "dawnlist_review.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, tr("export.title"), default,
            "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Tab", "Job ID", "Title", "Company", "Location",
                         "URL", "Verdict", "Reason", "Decision"])
            for tab, r in self._all_rows_by_tab():
                w.writerow([tab, r.job_id, r.title, r.company, r.location,
                            r.url, r.bucket, r.reason or r.screen_reason, ""])
        QMessageBox.information(self, tr("export.title"),
                                tr("export.done", count=sum(
                                    t.topLevelItemCount() for t in (
                                        self.shortlist, self.pursuing,
                                        self.rejected, self.screened_out,
                                        self.contained))))

    def _import_csv(self):
        import csv
        from PySide6.QtCore import QStandardPaths
        docs = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation)
        path, _ = QFileDialog.getOpenFileName(
            self, tr("import.title"), docs or "",
            "CSV (*.csv)")
        if not path:
            return
        applied = 0
        skipped = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                decision = (row.get("Decision") or "").strip().lower()
                if decision not in ("pursue", "reject", "later"):
                    continue
                job_id = (row.get("Job ID") or "").strip()
                if not job_id or job_id not in self._rows:
                    skipped.append(row.get("Title", job_id))
                    continue
                self.decided.emit(job_id, decision, "")
                applied += 1
                self._remove_from_trees(job_id, decision)
        self._refresh_tab_counts()
        self._show_detail()
        msg = tr("import.done", applied=applied)
        if skipped:
            msg += "\n" + tr("import.skipped", count=len(skipped))
        QMessageBox.information(self, tr("import.title"), msg)

    def _remove_from_trees(self, job_id: str, decision: str):
        """Remove a job from whichever tree it sits in; move to rejected if needed."""
        for tree in (self.shortlist, self.rejected,
                     self.screened_out, self.contained):
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                if item.data(0, Qt.UserRole) == job_id:
                    tree.takeTopLevelItem(i)
                    if decision == "reject":
                        self.rejected.addTopLevelItem(item)
                    return


def main(rows: list[ReviewRow] | None = None, counts: dict | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    from app.ui.branding import apply_icon
    apply_icon(app)
    w = ReviewWindow()
    w.load(rows or [], counts or {})
    w.show()
    return app.exec()
