"""End-to-end wiring: email account → outreach run, email account → voice."""
from unittest.mock import patch

import pytest

from app.core import db
from app.core.email_accounts import EmailAccount, Provider, save_email_account


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


# -- _imap_config ------------------------------------------------------------

def test_imap_config_returns_dict_when_account_and_password_exist(conn, fake_keyring):
    from app.core.email_accounts import store_email_password
    from app.main import _imap_config

    save_email_account(conn, EmailAccount(
        Provider.ICLOUD, "a@x.com", "imap.mail.me.com", 993))
    store_email_password("a@x.com", "s3cret")

    cfg = _imap_config(conn)
    assert cfg is not None
    assert cfg["host"] == "imap.mail.me.com"
    assert cfg["email"] == "a@x.com"
    assert cfg["password"] == "s3cret"


def test_imap_config_returns_none_without_account(conn):
    from app.main import _imap_config
    assert _imap_config(conn) is None


def test_imap_config_returns_none_without_password(conn, fake_keyring):
    from app.main import _imap_config

    save_email_account(conn, EmailAccount(
        Provider.GMAIL, "b@g.com", "imap.gmail.com"))
    assert _imap_config(conn) is None


# -- load_voice with IMAP ---------------------------------------------------

def test_load_voice_fetches_imap_when_account_configured(conn, fake_keyring,
                                                         tmp_path):
    from app.core.email_accounts import store_email_password
    from app.main import load_voice

    save_email_account(conn, EmailAccount(
        Provider.ICLOUD, "a@x.com", "imap.mail.me.com", 993))
    store_email_password("a@x.com", "pw")

    sent_bodies = [
        "Hi Jo, hope you are well. I wanted to follow up on our conversation.",
        "Dear Alice, thank you for your time last week.",
    ]
    with patch("app.core.email_client.fetch_sent_bodies",
               return_value=sent_bodies):
        profile = load_voice(conn, folder=tmp_path / "empty")

    assert profile.sample_size == 2


def test_load_voice_works_without_email_account(conn, tmp_path):
    from app.main import load_voice

    profile = load_voice(conn, folder=tmp_path / "empty")
    assert profile.sample_size == 0


def test_load_voice_survives_imap_failure(conn, fake_keyring, tmp_path):
    from app.core.email_accounts import store_email_password
    from app.main import load_voice

    save_email_account(conn, EmailAccount(
        Provider.GMAIL, "a@g.com", "imap.gmail.com"))
    store_email_password("a@g.com", "pw")

    with patch("app.core.email_client.fetch_sent_bodies",
               side_effect=OSError("connection refused")):
        profile = load_voice(conn, folder=tmp_path / "empty")

    assert profile.sample_size == 0, "IMAP failure never blocks voice"
