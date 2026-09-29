"""LinkedIn search query panel for Settings.

Reads the user's saved Dawnlist queries, generates optimised LinkedIn boolean
search strings, and presents them with copy-to-clipboard buttons.  The panel
is read-only — editing the underlying queries happens in SearchesPanel.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from app.i18n import tr
from app.linkedin.query_builder import LinkedInQuery, from_queries
from app.ui.onboarding import reflow
from app.ui.review import CREAM, TEAL


LINKEDIN_PANEL_STYLE = f"""
QWidget#linkedinCard {{
    background: #ffffff;
    border: 1px solid #cfcabf;
    border-radius: 6px;
    padding: 10px 12px;
}}
QLabel#queryLabel {{
    font-weight: 600;
    color: #2c3e50;
}}
QLabel#queryString {{
    font-family: Consolas, "Courier New", monospace;
    background: #f4f1ea;
    border: 1px solid #ddd8cc;
    border-radius: 4px;
    padding: 8px 10px;
    color: #45505a;
}}
QLabel#locationHint {{
    color: #8a8680;
    font-style: italic;
}}
QPushButton#copyBtn {{
    background: {TEAL};
    color: {CREAM};
    border: none;
    border-radius: 4px;
    padding: 4px 12px;
    font-weight: 600;
}}
QPushButton#copyBtn:hover {{
    background: #2a8e87;
}}
QLabel#copiedLabel {{
    color: {TEAL};
    font-weight: 600;
}}
"""


class _QueryCard(QWidget):
    def __init__(self, lq: LinkedInQuery, parent=None):
        super().__init__(parent)
        self.setObjectName("linkedinCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        label = QLabel(lq.label)
        label.setObjectName("queryLabel")
        top_row.addWidget(label)
        top_row.addStretch(1)

        btn = QPushButton(tr("linkedin.copy"))
        btn.setObjectName("copyBtn")
        btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._copied_label = QLabel(tr("linkedin.copied"))
        self._copied_label.setObjectName("copiedLabel")
        self._copied_label.hide()
        btn.clicked.connect(lambda: self._copy(lq.search_string))
        top_row.addWidget(self._copied_label)
        top_row.addWidget(btn)
        layout.addLayout(top_row)

        query_text = QLabel(lq.search_string)
        query_text.setObjectName("queryString")
        query_text.setWordWrap(True)
        query_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(query_text)

        if lq.location_hint:
            hint = QLabel(tr("linkedin.location_hint",
                where=lq.location_hint))
            hint.setObjectName("locationHint")
            layout.addWidget(hint)

    def _copy(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self._copied_label.show()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, self._copied_label.hide)


class LinkedInPanel(QWidget):
    def __init__(self, *, query_loader=None, parent=None):
        super().__init__(parent)
        self._load = query_loader or (lambda: [])

        self.setStyleSheet(LINKEDIN_PANEL_STYLE)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(10)

        heading = QLabel(tr("linkedin.heading"))
        heading.setObjectName("stepHeading")
        self._layout.addWidget(heading)

        body = QLabel(reflow(tr("linkedin.body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        self._layout.addWidget(body)

        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._layout.addWidget(self._cards_container)

        self._empty_label = QLabel(tr("linkedin.empty"))
        self._empty_label.setObjectName("stepBody")
        self._empty_label.setWordWrap(True)
        self._layout.addWidget(self._empty_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_refresh = QPushButton(tr("linkedin.refresh"))
        self._btn_refresh.setObjectName("secondary")
        self._btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(self._btn_refresh)

        self._btn_copy_all = QPushButton(tr("linkedin.copy_all"))
        self._btn_copy_all.setObjectName("copyBtn")
        self._btn_copy_all.clicked.connect(self._copy_all)
        btn_row.addWidget(self._btn_copy_all)
        self._layout.addLayout(btn_row)

        self._queries: list[LinkedInQuery] = []
        self.refresh()

    def refresh(self) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        raw = self._load()
        self._queries = from_queries(raw)

        self._empty_label.setVisible(not self._queries)
        self._cards_container.setVisible(bool(self._queries))
        self._btn_copy_all.setVisible(len(self._queries) > 1)

        for lq in self._queries:
            self._cards_layout.addWidget(_QueryCard(lq))

    def _copy_all(self) -> None:
        lines = []
        for lq in self._queries:
            lines.append(f"# {lq.label}")
            lines.append(lq.search_string)
            if lq.location_hint:
                lines.append(f"  Location: {lq.location_hint}")
            lines.append("")
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(lines).strip())
