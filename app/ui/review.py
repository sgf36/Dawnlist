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
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QSplitter, QTabWidget, QTextBrowser,
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


class FunnelBar(QWidget):
    """swept -> deduped -> gated -> screened -> assessed, always on screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(18)
        self.setStyleSheet(
            f"background:{TEAL}; color:{CREAM}; border-radius:6px;")
        self._labels: dict[str, QLabel] = {}

    def set_counts(self, counts: dict[str, int], *, incomplete_note: str = "") -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._labels.clear()

        stages = [("swept", "Swept"), ("deduped", "Deduped"),
                  ("gated_out", "Gated"), ("screened_likely", "Screened in"),
                  ("screened_out", "Screened out"), ("assessed", "Assessed")]
        for key, label in stages:
            if key not in counts:
                continue
            w = QLabel(f"<b>{counts[key]}</b><br><span style='font-size:11px'>{label}</span>")
            w.setTextFormat(Qt.RichText)
            w.setAlignment(Qt.AlignCenter)
            self._layout.addWidget(w)
            self._labels[key] = w

        self._layout.addStretch(1)

        # An incomplete run says so, in the bar, in gold. Never filed as normal.
        left = counts.get("left_unread", 0)
        if left or incomplete_note:
            text = f"⚠ {left} left unread" if left else "⚠ incomplete"
            if incomplete_note:
                text += f" — {incomplete_note}"
            warn = QLabel(text)
            warn.setStyleSheet(f"color:{GOLD}; font-weight:600;")
            self._layout.addWidget(warn)


class ReviewWindow(QMainWindow):
    decided = Signal(str, str)          # (job_id, decision)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dawnlist — this morning")
        self.resize(1180, 760)
        self._rows: dict[str, ReviewRow] = {}

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
        self.tabs.addTab(self.shortlist, "Shortlist")
        self.tabs.addTab(self.rejected, "Rejected")
        # spec 5.4: the unlikely pile is browsable, never erased.
        self.tabs.addTab(self.screened_out, "Screened out")
        self.tabs.addTab(self.contained, "Needs review")
        splitter.addWidget(self.tabs)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        rl.addWidget(self.detail, 1)

        buttons = QHBoxLayout()
        self.btn_pursue = QPushButton("Pursue")
        self.btn_later = QPushButton("Later")
        self.btn_reject = QPushButton("Reject — permanently")
        self.btn_pursue.setStyleSheet(
            f"background:{TEAL}; color:{CREAM}; padding:8px 16px; border-radius:5px;")
        self.btn_reject.setStyleSheet(
            f"background:{GOLD_DEEP}; color:{CREAM}; padding:8px 16px; border-radius:5px;")
        for b in (self.btn_pursue, self.btn_later, self.btn_reject):
            buttons.addWidget(b)
        buttons.addStretch(1)
        rl.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setSizes([520, 660])
        outer.addWidget(splitter, 1)

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
        t.setHeaderLabels(["Title", "Company", "Why"])
        t.setColumnWidth(0, 230)
        t.setColumnWidth(1, 140)
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
            if r.bucket == "strong":
                f = QFont()
                f.setBold(True)
                item.setFont(0, f)
            if r.downgrade_reason or r.contained:
                item.setForeground(2, Qt.darkYellow)
            target.addTopLevelItem(item)

        self.funnel.set_counts(counts, incomplete_note=incomplete_note)
        self.tabs.setTabText(0, f"Shortlist ({self.shortlist.topLevelItemCount()})")
        self.tabs.setTabText(1, f"Rejected ({self.rejected.topLevelItemCount()})")
        self.tabs.setTabText(2, f"Screened out ({self.screened_out.topLevelItemCount()})")
        self.tabs.setTabText(3, f"Needs review ({self.contained.topLevelItemCount()})")

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
            parts.append(f"<p><a href='{r.url}'>Open the original posting</a></p>")
        verdict = f"{r.bucket} — {r.reason}" if r.reason else r.bucket
        parts.append(f"<p><b>Verdict:</b> {verdict}</p>")
        if r.disqualifying_quote:
            parts.append(
                f"<blockquote style='border-left:3px solid {GOLD};padding-left:8px;"
                f"color:#333'>{r.disqualifying_quote}</blockquote>")
        if not r.requirement_checked:
            parts.append("<p style='color:#8a6d3b'><b>Not checked</b> — the "
                         "description was truncated or silent on this. Treated "
                         "as unknown, never as a failure.</p>")
        if r.downgrade_reason:
            parts.append(f"<p style='color:#8a6d3b'><b>Downgraded:</b> "
                         f"{r.downgrade_reason}</p>")
        if r.contained:
            parts.append("<p style='color:#8a6d3b'><b>Contained match</b> — a "
                         "kill term matched inside a longer role name. Check "
                         "this before trusting the kill.</p>")
        if r.screen_reason:
            parts.append(f"<p style='color:#666'><i>Screen: {r.screen_reason}</i></p>")
        parts.append("<hr>")
        parts.append(f"<div style='white-space:pre-wrap'>{r.description}</div>")
        self.detail.setHtml("".join(parts))

    def _decide(self, decision: str):
        r = self._current_row()
        if r is not None:
            self.decided.emit(r.job_id, decision)


def main(rows: list[ReviewRow] | None = None, counts: dict | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    w = ReviewWindow()
    w.load(rows or [], counts or {})
    w.show()
    return app.exec()
