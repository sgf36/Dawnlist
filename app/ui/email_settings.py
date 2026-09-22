"""Settings panel for connecting an email account (IMAP).

Follows the same layout and dependency-injection pattern as `KeyPanel` and
`LicencePanel` in `app/ui/settings.py`.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from app.core.email_accounts import (
    PROVIDER_DEFAULTS, EmailAccount, Provider,
    delete_email_account, delete_email_password,
    load_email_account, read_email_password,
    save_email_account, store_email_password,
)
from app.i18n import tr
from app.ui.background import run_in_background
from app.ui.onboarding import reflow


_PROVIDER_LABELS = {
    Provider.ICLOUD: "iCloud",
    Provider.GMAIL: "Gmail",
    Provider.EXCHANGE: "Exchange / Outlook 365",
    Provider.GENERIC: "Other IMAP",
}


class EmailPanel(QWidget):
    """Configure an IMAP email account for placing drafts and learning voice."""

    changed = Signal()

    def __init__(self, *, conn=None, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._task = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(tr("settings.email_heading"))
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)

        body = QLabel(reflow(tr("settings.email_body")))
        body.setObjectName("stepBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        # Provider
        prov_row = QHBoxLayout()
        prov_row.setSpacing(10)
        prov_label = QLabel(tr("settings.email_provider"))
        self.provider_combo = QComboBox()
        for p in Provider:
            self.provider_combo.addItem(_PROVIDER_LABELS[p], p.value)
        prov_row.addWidget(prov_label)
        prov_row.addWidget(self.provider_combo, 1)
        layout.addLayout(prov_row)

        # Email address
        self.email_field = QLineEdit()
        self.email_field.setObjectName("sentence")
        self.email_field.setPlaceholderText(tr("settings.email_address_placeholder"))
        layout.addWidget(self.email_field)

        # IMAP host / port
        server_row = QHBoxLayout()
        server_row.setSpacing(10)
        self.host_field = QLineEdit()
        self.host_field.setObjectName("sentence")
        self.host_field.setPlaceholderText(tr("settings.email_host_placeholder"))
        self.port_field = QLineEdit()
        self.port_field.setObjectName("sentence")
        self.port_field.setPlaceholderText("993")
        self.port_field.setMaximumWidth(80)
        server_row.addWidget(self.host_field, 1)
        server_row.addWidget(self.port_field)
        layout.addLayout(server_row)

        # Password (app-specific password)
        self.password_field = QLineEdit()
        self.password_field.setObjectName("sentence")
        self.password_field.setEchoMode(QLineEdit.Password)
        self.password_field.setPlaceholderText(
            tr("settings.email_password_placeholder"))
        layout.addWidget(self.password_field)

        # Status line
        self.stored = QLabel()
        self.stored.setObjectName("storedKey")
        layout.addWidget(self.stored)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_test = QPushButton(tr("settings.email_test"))
        self.btn_test.setObjectName("secondary")
        self.btn_test.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_save = QPushButton(tr("settings.email_save"))
        self.btn_save.setObjectName("primary")
        self.btn_save.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_remove = QPushButton(tr("settings.email_remove"))
        self.btn_remove.setObjectName("secondary")
        self.btn_remove.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        btn_row.addWidget(self.btn_test)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_remove)
        layout.addLayout(btn_row)

        # Result feedback
        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)

        # Signals
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.btn_test.clicked.connect(self._test)
        self.btn_save.clicked.connect(self._save)
        self.btn_remove.clicked.connect(self._remove)

        from app.ui.settings import SETTINGS_STYLESHEET
        self.setStyleSheet(SETTINGS_STYLESHEET)
        self.refresh()

    def refresh(self) -> None:
        if self._conn is None:
            self.stored.setText("")
            self.btn_remove.setVisible(False)
            return
        account = load_email_account(self._conn)
        if account:
            self.stored.setText(
                tr("settings.email_stored", email=account.email))
            self.btn_remove.setVisible(True)
            idx = self.provider_combo.findData(account.provider.value)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            self.email_field.setText(account.email)
            self.host_field.setText(account.imap_host)
            self.port_field.setText(str(account.imap_port))
        else:
            self.stored.setText(tr("settings.email_none"))
            self.btn_remove.setVisible(False)

    def _provider_changed(self, _index: int) -> None:
        provider = Provider(self.provider_combo.currentData())
        defaults = PROVIDER_DEFAULTS.get(provider)
        if defaults and not self.host_field.text().strip():
            self.host_field.setText(defaults["imap_host"])
            self.port_field.setText(str(defaults["imap_port"]))
        elif defaults:
            self.host_field.setPlaceholderText(defaults["imap_host"])

    def _say(self, text: str, ok: bool) -> None:
        self.result.setObjectName("ok" if ok else "bad")
        self.result.setText(text)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.result.setVisible(bool(text))

    def _busy(self, on: bool) -> None:
        self.btn_test.setEnabled(not on)
        self.btn_save.setEnabled(not on)
        self.btn_remove.setEnabled(not on)

    def _read_fields(self) -> tuple[str, str, str, int, str]:
        """Return (provider_value, email, host, port, password)."""
        provider = self.provider_combo.currentData()
        email = self.email_field.text().strip()
        host = self.host_field.text().strip()
        try:
            port = int(self.port_field.text().strip() or "993")
        except ValueError:
            port = 993
        password = self.password_field.text()
        return provider, email, host, port, password

    def _test(self) -> None:
        provider, email, host, port, password = self._read_fields()
        if not email or not host:
            self._say(tr("settings.email_incomplete"), False)
            return
        if not password:
            if self._conn is not None:
                try:
                    password = read_email_password(email) or ""
                except Exception:
                    pass
            if not password:
                self._say(tr("settings.email_no_password"), False)
                return
        self._busy(True)
        self._say(tr("settings.email_testing"), True)

        def work():
            from app.core.email_client import check_connection
            return check_connection(host, port, email, password)

        self._task = run_in_background(
            work,
            on_done=self._tested,
            on_error=lambda exc: self._tested_error(str(exc)))

    def _tested(self, result) -> None:
        self._busy(False)
        if result.ok:
            if result.message:
                self._say(tr("settings.email_test_ok_folder",
                             folder=result.message), True)
            else:
                self._say(tr("settings.email_test_ok"), True)
        else:
            self._say(tr("settings.email_test_failed",
                         error=result.message), False)

    def _tested_error(self, msg: str) -> None:
        self._busy(False)
        self._say(tr("settings.email_test_failed", error=msg), False)

    def _save(self) -> None:
        provider_val, email, host, port, password = self._read_fields()
        if not email or not host:
            self._say(tr("settings.email_incomplete"), False)
            return
        if not password:
            if self._conn is not None:
                try:
                    password = read_email_password(email) or ""
                except Exception:
                    pass
        if not password:
            self._say(tr("settings.email_no_password"), False)
            return
        account = EmailAccount(
            provider=Provider(provider_val),
            email=email,
            imap_host=host,
            imap_port=port,
        )
        if self._conn is not None:
            save_email_account(self._conn, account)
        try:
            store_email_password(email, password)
        except Exception:
            self._say(tr("settings.email_store_failed"), False)
            return
        self.password_field.clear()
        self._say(tr("settings.email_saved"), True)
        self.refresh()
        self.changed.emit()

    def _remove(self) -> None:
        if self._conn is None:
            return
        account = load_email_account(self._conn)
        if account:
            delete_email_password(account.email)
        delete_email_account(self._conn)
        self.email_field.clear()
        self.host_field.clear()
        self.port_field.clear()
        self.password_field.clear()
        self._say(tr("settings.email_removed"), True)
        self.refresh()
        self.changed.emit()
