"""Email account configuration — store, load and validate.

Credentials live in the operating system's credential store (Credential
Manager on Windows, Keychain on a Mac), never in SQLite.  The settings table
holds everything else: provider, server, port.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import Enum


class Provider(str, Enum):
    ICLOUD = "icloud"
    GMAIL = "gmail"
    EXCHANGE = "exchange"
    GENERIC = "generic"


PROVIDER_DEFAULTS: dict[Provider, dict] = {
    Provider.ICLOUD: {
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
    },
    Provider.GMAIL: {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
    },
    Provider.EXCHANGE: {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
    },
}

KEYRING_SERVICE = "dawnlist-email"
EMAIL_ACCOUNT_KEY = "email_account"


@dataclass
class EmailAccount:
    provider: Provider
    email: str
    imap_host: str
    imap_port: int = 993

    def to_json(self) -> str:
        return json.dumps({
            "provider": self.provider.value,
            "email": self.email,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
        })

    @classmethod
    def from_json(cls, text: str) -> EmailAccount:
        d = json.loads(text)
        return cls(
            provider=Provider(d["provider"]),
            email=d["email"],
            imap_host=d["imap_host"],
            imap_port=d.get("imap_port", 993),
        )


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def load_email_account(conn: sqlite3.Connection) -> EmailAccount | None:
    raw = _get(conn, EMAIL_ACCOUNT_KEY)
    if not raw:
        return None
    try:
        return EmailAccount.from_json(raw)
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def save_email_account(conn: sqlite3.Connection,
                       account: EmailAccount) -> None:
    _set(conn, EMAIL_ACCOUNT_KEY, account.to_json())


def delete_email_account(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key=?", (EMAIL_ACCOUNT_KEY,))
    conn.commit()


def store_email_password(email: str, password: str) -> None:
    from app.core.credentials import write
    write(KEYRING_SERVICE, email, password)


def read_email_password(email: str) -> str | None:
    from app.core.credentials import read
    return read(KEYRING_SERVICE, email)


def delete_email_password(email: str) -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, email)
    except Exception:  # noqa: BLE001
        pass
