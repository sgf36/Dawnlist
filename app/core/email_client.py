"""IMAP client for placing drafts and reading sent messages.

Uses IMAP APPEND to the Drafts folder, not SMTP.  The human's send click
in their own mail client remains the authorisation boundary.

Also fetches recent sent messages so the voice profile can learn the
user's tone without them exporting .eml files by hand.
"""
from __future__ import annotations

import imaplib
import ssl
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage


@dataclass
class IMAPResult:
    ok: bool
    message: str


DRAFTS_FOLDER_NAMES = ("Drafts", "INBOX.Drafts", "[Gmail]/Drafts", "Draft")
SENT_FOLDER_NAMES = ("Sent", "INBOX.Sent", "[Gmail]/Sent Mail",
                     "Sent Items", "Sent Messages")


def _connect(host: str, port: int, email: str,
             password: str) -> imaplib.IMAP4_SSL:
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    conn.login(email, password)
    return conn


def _find_folder_by_flag(conn: imaplib.IMAP4_SSL,
                         flag: str,
                         fallbacks: tuple[str, ...]) -> str | None:
    """Find a mailbox by its special-use flag, falling back to common names."""
    status, data = conn.list()
    if status == "OK" and data:
        for line in data:
            if not isinstance(line, bytes):
                continue
            decoded = line.decode("utf-8", errors="replace")
            if flag in decoded:
                parts = decoded.rsplit('"', 2)
                if len(parts) >= 2:
                    return parts[-2]
    for name in fallbacks:
        status, _ = conn.select(f'"{name}"', readonly=True)
        if status == "OK":
            conn.close()
            return name
    return None


def _find_drafts_folder(conn: imaplib.IMAP4_SSL) -> str | None:
    return _find_folder_by_flag(conn, "\\Drafts", DRAFTS_FOLDER_NAMES)


def _find_sent_folder(conn: imaplib.IMAP4_SSL) -> str | None:
    return _find_folder_by_flag(conn, "\\Sent", SENT_FOLDER_NAMES)


def check_connection(host: str, port: int, email: str,
                     password: str) -> IMAPResult:
    try:
        conn = _connect(host, port, email, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        return IMAPResult(False, str(exc))
    try:
        folder = _find_drafts_folder(conn)
        if folder:
            return IMAPResult(True, folder)
        return IMAPResult(True, "")
    except (imaplib.IMAP4.error, OSError) as exc:
        return IMAPResult(False, str(exc))
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def place_draft(host: str, port: int, email: str, password: str,
                msg: EmailMessage) -> IMAPResult:
    """IMAP APPEND a message to the Drafts folder as an unsent draft."""
    try:
        conn = _connect(host, port, email, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        return IMAPResult(False, str(exc))
    try:
        folder = _find_drafts_folder(conn)
        if not folder:
            return IMAPResult(False, "no Drafts folder found")
        raw = bytes(msg)
        status, detail = conn.append(f'"{folder}"', "\\Draft", None, raw)
        if status == "OK":
            return IMAPResult(True, "draft placed")
        return IMAPResult(False, f"APPEND failed: {detail}")
    except (imaplib.IMAP4.error, OSError) as exc:
        return IMAPResult(False, str(exc))
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_sent_bodies(host: str, port: int, email: str, password: str,
                      limit: int = 50) -> list[str]:
    """Fetch plain-text bodies from the most recent sent messages.

    Returns up to `limit` message bodies for voice-profile extraction.
    Never modifies the mailbox.
    """
    bodies: list[str] = []
    try:
        conn = _connect(host, port, email, password)
    except (imaplib.IMAP4.error, OSError):
        return bodies
    try:
        folder = _find_sent_folder(conn)
        if not folder:
            return bodies
        conn.select(f'"{folder}"', readonly=True)
        status, data = conn.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            conn.close()
            return bodies
        ids = data[0].split()
        recent = ids[-limit:] if len(ids) > limit else ids
        for mid in reversed(recent):
            status, msg_data = conn.fetch(mid, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            for part in msg_data:
                if isinstance(part, tuple):
                    msg = message_from_bytes(part[1], policy=policy.default)
                    body = _extract_text(msg)
                    if body:
                        bodies.append(body)
                    break
        conn.close()
    except (imaplib.IMAP4.error, OSError):
        pass
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return bodies


def _extract_text(msg) -> str:
    """Pull the plain-text body from a parsed message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_content()
                if isinstance(payload, str):
                    return payload.strip()
    else:
        ct = msg.get_content_type()
        if ct == "text/plain":
            payload = msg.get_content()
            if isinstance(payload, str):
                return payload.strip()
    return ""
