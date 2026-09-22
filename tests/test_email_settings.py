"""Email settings panel: layout, wiring and the window integration."""
import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.core import db  # noqa: E402
from app.core.email_accounts import (  # noqa: E402
    EmailAccount,
    Provider,
    save_email_account,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


def test_email_panel_shows_the_heading(qapp, conn):
    from app.ui.email_settings import EmailPanel

    panel = EmailPanel(conn=conn)
    headings = [lbl for lbl in panel.findChildren(QLabel)
                if lbl.objectName() == "stepHeading"]
    assert len(headings) == 1
    assert headings[0].text()
    panel.close()


def test_email_panel_remove_hidden_when_no_account(qapp, conn):
    from app.ui.email_settings import EmailPanel

    panel = EmailPanel(conn=conn)
    assert panel.btn_remove.isHidden()
    panel.close()


def test_email_panel_remove_shown_with_stored_account(qapp, conn):
    from app.ui.email_settings import EmailPanel

    save_email_account(conn, EmailAccount(
        Provider.ICLOUD, "a@x.com", "imap.mail.me.com"))
    panel = EmailPanel(conn=conn)
    assert not panel.btn_remove.isHidden()
    panel.close()


def test_provider_combo_populates_host_on_selection(qapp, conn):
    from app.ui.email_settings import EmailPanel

    panel = EmailPanel(conn=conn)
    panel.provider_combo.setCurrentIndex(
        panel.provider_combo.findData("gmail"))
    assert panel.host_field.text() == "imap.gmail.com" or \
        panel.host_field.placeholderText() == "imap.gmail.com"
    panel.close()


def test_settings_window_carries_email_panel_when_given(qapp, conn):
    from app.ui.email_settings import EmailPanel
    from app.ui.settings import SettingsWindow

    panel = EmailPanel(conn=conn)
    w = SettingsWindow(variant="direct", email=panel)
    assert w.email is panel
    assert panel.parent() is not None, "it is laid out, not merely held"
    w.close()


def test_settings_window_omits_email_panel_without_it(qapp):
    from app.ui.settings import SettingsWindow

    w = SettingsWindow(variant="direct")
    assert w.email is None
    w.close()
