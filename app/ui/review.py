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

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QFont, QFontMetrics
from app.i18n import is_rtl, tr
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMainWindow,
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

#: The warning chip must never push the funnel counts off the bar.
WARNING_MAX_WIDTH = 420

BUCKET_ORDER = {"strong": 0, "possible": 1, "judgement-call": 2, "rejected": 3}


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
QTabBar::tab {{ padding: 8px 14px; }}
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

    def set_counts(self, counts: dict[str, int], *, incomplete_note: str = "") -> None:
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
            warn = QLabel()
            warn.setObjectName("funnelWarning")
            warn.setToolTip(text)
            warn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            warn.setMaximumWidth(WARNING_MAX_WIDTH)
            # QLabel clips rather than elides, so elide the string ourselves.
            metrics = QFontMetrics(warn.font())
            warn.setText(metrics.elidedText(
                text, Qt.ElideRight, WARNING_MAX_WIDTH - 28))
            self._layout.addWidget(warn)


class ReviewWindow(QMainWindow):
    decided = Signal(str, str)          # (job_id, decision)
    settings_requested = Signal()
    #: Job-alert emails the user dropped on the window. The listing promises
    #: "add job-alert emails yourself for anything the feeds miss", and the
    #: parser for them was complete and reachable from nowhere.
    alerts_dropped = Signal(list)

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

        self.act_settings = QAction(tr("settings.title"), self)
        self.act_settings.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.act_settings.triggered.connect(self.settings_requested)
        self.menuBar().addMenu(tr("menu.app")).addAction(self.act_settings)

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

        splitter = QSplitter(Qt.Horizontal)

        self.tabs = QTabWidget()
        self.shortlist = self._make_tree()
        self.rejected = self._make_tree()
        self.screened_out = self._make_tree()
        self.contained = self._make_tree()
        self.tabs.addTab(self.shortlist, tr("tab.shortlist"))
        self.tabs.addTab(self.rejected, tr("tab.rejected"))
        # spec 5.4: the unlikely pile is browsable, never erased.
        self.tabs.addTab(self.screened_out, tr("tab.screened_out"))
        self.tabs.addTab(self.contained, tr("tab.needs_review"))
        splitter.addWidget(self.tabs)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
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

        for tree in (self.shortlist, self.rejected, self.screened_out, self.contained):
            tree.currentItemChanged.connect(self._show_detail)
        self.btn_pursue.clicked.connect(lambda: self._decide("pursue"))
        self.btn_later.clicked.connect(lambda: self._decide("later"))
        self.btn_reject.clicked.connect(lambda: self._decide("reject"))

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
        return t

    def load(self, rows: list[ReviewRow], counts: dict[str, int], *,
             incomplete_note: str = "") -> None:
        self._rows = {r.job_id: r for r in rows}
        for tree in (self.shortlist, self.rejected, self.screened_out, self.contained):
            tree.clear()

        ordered = sorted(rows, key=lambda r: (BUCKET_ORDER.get(r.bucket, 9),
                                              r.company.casefold()))
        for r in ordered:
            if r.screen_reason and r.bucket == "screened-out":
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
            target.addTopLevelItem(item)

        self.funnel.set_counts(counts, incomplete_note=incomplete_note)
        for idx, (tree, key) in enumerate((
                (self.shortlist, "tab.shortlist"), (self.rejected, "tab.rejected"),
                (self.screened_out, "tab.screened_out"),
                (self.contained, "tab.needs_review"))):
            self.tabs.setTabText(idx, tr("tab.with_count", label=tr(key),
                                         count=tree.topLevelItemCount()))

    # -- interaction -------------------------------------------------------
    def _current_row(self) -> ReviewRow | None:
        tree = self.tabs.currentWidget()
        item = tree.currentItem() if isinstance(tree, QTreeWidget) else None
        if item is None:
            return None
        return self._rows.get(item.data(0, Qt.UserRole))

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
        parts.append(f"<div style='white-space:pre-wrap'>{r.description}</div>")
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
        r = self._current_row()
        if r is not None:
            self.decided.emit(r.job_id, decision)


def main(rows: list[ReviewRow] | None = None, counts: dict | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    from app.ui.branding import apply_icon
    apply_icon(app)
    w = ReviewWindow()
    w.load(rows or [], counts or {})
    w.show()
    return app.exec()
