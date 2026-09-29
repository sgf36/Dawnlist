"""Dialog for importing jobs from LinkedIn URLs.

Individual: paste a single URL and click Import.
Bulk: paste multiple URLs (one per line) or the contents of a job-alert
      email, and import all recognised LinkedIn job URLs at once.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QSizePolicy, QVBoxLayout)

from app.i18n import tr
from app.linkedin.import_jobs import DAILY_IMPORT_CAP
from app.ui.review import CREAM, GOLD_DEEP, TEAL


DIALOG_STYLE = f"""
QDialog {{
    background: {CREAM};
}}
QLabel#heading {{
    font-size: 16px;
    font-weight: 600;
    color: #2c3e50;
}}
QLabel#hint {{
    color: #8a8680;
}}
QLabel#capLabel {{
    color: #6b7480;
    font-size: 12px;
}}
QLabel#errorLabel {{
    color: {GOLD_DEEP};
    font-weight: 600;
}}
QLabel#successLabel {{
    color: {TEAL};
    font-weight: 600;
}}
QPlainTextEdit#urlInput {{
    font-family: Consolas, "Courier New", monospace;
    background: #ffffff;
    border: 1px solid #cfcabf;
    border-radius: 6px;
    padding: 8px;
    font-size: 13px;
}}
QPushButton#importBtn {{
    background: {TEAL};
    color: {CREAM};
    border: none;
    border-radius: 4px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 14px;
}}
QPushButton#importBtn:hover {{
    background: #2a8e87;
}}
QPushButton#importBtn:disabled {{
    background: #b0b0b0;
}}
"""


class LinkedInImportDialog(QDialog):
    """Modal dialog for pasting LinkedIn job URLs."""

    imported = Signal(list)

    def __init__(self, *, conn=None, remaining: int = DAILY_IMPORT_CAP,
                 parent=None):
        super().__init__(parent)
        self._conn = conn
        self._remaining = remaining

        self.setWindowTitle(tr("linkedin_import.title"))
        self.setMinimumSize(520, 380)
        self.setStyleSheet(DIALOG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel(tr("linkedin_import.heading"))
        heading.setObjectName("heading")
        layout.addWidget(heading)

        hint = QLabel(tr("linkedin_import.hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._input = QPlainTextEdit()
        self._input.setObjectName("urlInput")
        self._input.setPlaceholderText(tr("linkedin_import.placeholder"))
        self._input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._input, 1)

        self._cap_label = QLabel(
            tr("linkedin_import.remaining",
               remaining=self._remaining, cap=DAILY_IMPORT_CAP))
        self._cap_label.setObjectName("capLabel")
        layout.addWidget(self._cap_label)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.hide()
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._url_count = QLabel("")
        self._url_count.setObjectName("hint")
        btn_row.addWidget(self._url_count)

        self._btn_import = QPushButton(tr("linkedin_import.import_btn"))
        self._btn_import.setObjectName("importBtn")
        self._btn_import.setEnabled(False)
        self._btn_import.clicked.connect(self._do_import)
        btn_row.addWidget(self._btn_import)
        layout.addLayout(btn_row)

    def _on_text_changed(self):
        from app.linkedin.import_jobs import parse_url_list
        text = self._input.toPlainText()
        urls = parse_url_list(text)
        count = len(urls)

        if count == 0:
            self._url_count.setText("")
            self._btn_import.setEnabled(False)
        elif count > self._remaining:
            self._url_count.setText(
                tr("linkedin_import.too_many",
                    count=count, remaining=self._remaining))
            self._url_count.setObjectName("errorLabel")
            self._url_count.setStyleSheet(f"color: {GOLD_DEEP}; font-weight: 600;")
            self._btn_import.setEnabled(False)
        else:
            self._url_count.setText(
                tr("linkedin_import.found_urls", count=count))
            self._url_count.setObjectName("hint")
            self._url_count.setStyleSheet("color: #8a8680;")
            self._btn_import.setEnabled(True)

        self._status.hide()

    def _do_import(self):
        from app.linkedin.import_jobs import (DailyCapExceeded, fetch_many,
                                              parse_url_list)

        self._btn_import.setEnabled(False)
        self._btn_import.setText(tr("linkedin_import.importing"))

        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        urls = parse_url_list(self._input.toPlainText())
        try:
            results = fetch_many(urls, conn=self._conn)
        except DailyCapExceeded as e:
            self._status.setText(
                tr("linkedin_import.cap_exceeded",
                    used=e.used, cap=e.cap))
            self._status.setObjectName("errorLabel")
            self._status.setStyleSheet(f"color: {GOLD_DEEP}; font-weight: 600;")
            self._status.show()
            self._btn_import.setEnabled(True)
            self._btn_import.setText(tr("linkedin_import.import_btn"))
            return

        imported = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]

        if imported:
            self._remaining -= len(imported)
            self._cap_label.setText(
                tr("linkedin_import.remaining",
                    remaining=max(0, self._remaining), cap=DAILY_IMPORT_CAP))

        lines: list[str] = []
        if imported:
            lines.append(tr("linkedin_import.success",
                count=len(imported)))
        if failed:
            for r in failed:
                lines.append(f"• {r.url}: {r.error}")

        self._status.setText("\n".join(lines))
        self._status.setObjectName("successLabel" if not failed else "errorLabel")
        self._status.setStyleSheet(
            f"color: {TEAL}; font-weight: 600;" if not failed
            else f"color: {GOLD_DEEP};")
        self._status.show()

        self._btn_import.setText(tr("linkedin_import.import_btn"))
        self._btn_import.setEnabled(False)
        self._input.clear()

        if imported:
            self.imported.emit([r.job for r in imported if r.job])
