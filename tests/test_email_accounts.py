"""Email account model: serialisation, settings-table persistence, credentials."""
import json

import pytest

from app.core import db
from app.core.email_accounts import (
    EMAIL_ACCOUNT_KEY,
    EmailAccount,
    Provider,
    delete_email_account,
    load_email_account,
    save_email_account,
)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


# -- round-trip serialisation ------------------------------------------------

def test_email_account_serialises_to_json_and_back():
    account = EmailAccount(
        provider=Provider.ICLOUD,
        email="alice@icloud.com",
        imap_host="imap.mail.me.com",
        imap_port=993,
    )
    restored = EmailAccount.from_json(account.to_json())
    assert restored.provider == Provider.ICLOUD
    assert restored.email == "alice@icloud.com"
    assert restored.imap_host == "imap.mail.me.com"
    assert restored.imap_port == 993


def test_from_json_uses_default_port_when_absent():
    raw = json.dumps({
        "provider": "gmail",
        "email": "bob@gmail.com",
        "imap_host": "imap.gmail.com",
    })
    account = EmailAccount.from_json(raw)
    assert account.imap_port == 993


# -- database persistence ---------------------------------------------------

def test_save_and_load_email_account(conn):
    account = EmailAccount(
        provider=Provider.GMAIL,
        email="bob@gmail.com",
        imap_host="imap.gmail.com",
        imap_port=993,
    )
    save_email_account(conn, account)
    loaded = load_email_account(conn)
    assert loaded is not None
    assert loaded.email == "bob@gmail.com"
    assert loaded.provider == Provider.GMAIL


def test_load_returns_none_when_no_account_stored(conn):
    assert load_email_account(conn) is None


def test_save_overwrites_previous_account(conn):
    first = EmailAccount(Provider.ICLOUD, "a@x.com", "imap.x.com")
    second = EmailAccount(Provider.EXCHANGE, "b@y.com", "outlook.office365.com")
    save_email_account(conn, first)
    save_email_account(conn, second)
    loaded = load_email_account(conn)
    assert loaded.email == "b@y.com"
    assert loaded.provider == Provider.EXCHANGE


def test_delete_email_account(conn):
    account = EmailAccount(Provider.GENERIC, "c@z.com", "mail.z.com")
    save_email_account(conn, account)
    assert load_email_account(conn) is not None
    delete_email_account(conn)
    assert load_email_account(conn) is None


def test_load_tolerates_corrupt_json(conn):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?)",
        (EMAIL_ACCOUNT_KEY, "not valid json"))
    conn.commit()
    assert load_email_account(conn) is None


# -- credential storage (mocked keyring) ------------------------------------

def test_store_and_read_email_password(fake_keyring):
    from app.core.email_accounts import read_email_password, store_email_password
    store_email_password("test@example.com", "s3cret")
    assert read_email_password("test@example.com") == "s3cret"


# -- provider defaults -------------------------------------------------------

def test_provider_defaults_cover_the_three_named_providers():
    from app.core.email_accounts import PROVIDER_DEFAULTS
    for p in (Provider.ICLOUD, Provider.GMAIL, Provider.EXCHANGE):
        defaults = PROVIDER_DEFAULTS[p]
        assert "imap_host" in defaults
        assert "imap_port" in defaults
    assert Provider.GENERIC not in PROVIDER_DEFAULTS
