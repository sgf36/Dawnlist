"""The credential store: "no such entry" and "no store at all" are not the same.

Every caller used to swallow keyring errors into None, so a locked or broken
store read as a missing key — a licence check then told a paying customer they
had no licence, and a save that failed escaped into a button handler after a
single-use code had been spent.
"""
import keyring
import pytest

from app.core import credentials
from app.core.credentials import KeyringUnavailable


def _broken(*_args):
    from keyring.errors import NoKeyringError
    raise NoKeyringError("no recommended backend")


def test_a_missing_entry_is_none_not_an_error(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", lambda *a: None)
    assert credentials.read("svc", "acct") is None


def test_a_stored_entry_is_returned(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", lambda *a: "secret")
    assert credentials.read("svc", "acct") == "secret"


def test_a_broken_store_raises_instead_of_reading_as_empty(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", _broken)
    with pytest.raises(KeyringUnavailable):
        credentials.read("svc", "acct")


def test_a_refused_write_raises(monkeypatch):
    monkeypatch.setattr(keyring, "set_password", _broken)
    with pytest.raises(KeyringUnavailable):
        credentials.write("svc", "acct", "value")


def test_a_good_write_reaches_the_store(monkeypatch):
    seen = []
    monkeypatch.setattr(keyring, "set_password", lambda *a: seen.append(a))
    credentials.write("svc", "acct", "value")
    assert seen == [("svc", "acct", "value")]


def test_saving_the_anthropic_key_raises_rather_than_passing_silently(monkeypatch):
    from app.core import api_key

    monkeypatch.setattr(keyring, "set_password", _broken)
    with pytest.raises(KeyringUnavailable):
        api_key.store("sk-ant-api03-" + "x" * 40)
