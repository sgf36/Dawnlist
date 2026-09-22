"""IMAP client: connection, draft placement and sent-message fetching.

All network calls are stubbed — no real IMAP server is ever contacted.
"""
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from app.core.email_client import (
    IMAPResult,
    _extract_text,
    _find_drafts_folder,
    _find_sent_folder,
    place_draft,
    check_connection,
    fetch_sent_bodies,
)


# -- helpers -----------------------------------------------------------------

def _mock_imap(list_data=None, select_ok=True):
    """Build a mock IMAP4_SSL with configurable LIST and SELECT responses."""
    conn = MagicMock()
    if list_data is not None:
        conn.list.return_value = ("OK", list_data)
    else:
        conn.list.return_value = ("OK", [])
    if select_ok:
        conn.select.return_value = ("OK", [b"1"])
    else:
        conn.select.return_value = ("NO", [b"fail"])
    conn.close.return_value = ("OK", [])
    conn.logout.return_value = ("BYE", [])
    return conn


# -- folder detection --------------------------------------------------------

def test_find_drafts_folder_by_special_use_flag():
    conn = _mock_imap([b'(\\HasNoChildren \\Drafts) "/" "Drafts"'])
    folder = _find_drafts_folder(conn)
    assert folder == "Drafts"


def test_find_drafts_folder_falls_back_to_name():
    conn = _mock_imap([b'(\\HasNoChildren) "/" "INBOX"'])
    conn.select.return_value = ("OK", [b"0"])
    folder = _find_drafts_folder(conn)
    assert folder == "Drafts"


def test_find_sent_folder_by_special_use_flag():
    conn = _mock_imap([b'(\\HasNoChildren \\Sent) "/" "Sent"'])
    folder = _find_sent_folder(conn)
    assert folder == "Sent"


def test_find_drafts_returns_none_when_nothing_matches():
    conn = _mock_imap([b'(\\HasNoChildren) "/" "INBOX"'])
    conn.select.return_value = ("NO", [b"fail"])
    folder = _find_drafts_folder(conn)
    assert folder is None


# -- test_connection ---------------------------------------------------------

@patch("app.core.email_client._connect")
def test_check_connection_success(mock_connect):
    mock_conn = _mock_imap([b'(\\Drafts) "/" "Drafts"'])
    mock_connect.return_value = mock_conn
    result = check_connection("imap.example.com", 993, "u@x.com", "pw")
    assert result.ok
    assert result.message == "Drafts"


@patch("app.core.email_client._connect")
def test_check_connection_auth_failure(mock_connect):
    import imaplib
    mock_connect.side_effect = imaplib.IMAP4.error("LOGIN failed")
    result = check_connection("imap.example.com", 993, "u@x.com", "bad")
    assert not result.ok
    assert "LOGIN failed" in result.message


@patch("app.core.email_client._connect")
def test_check_connection_network_failure(mock_connect):
    mock_connect.side_effect = OSError("connection refused")
    result = check_connection("imap.example.com", 993, "u@x.com", "pw")
    assert not result.ok
    assert "connection refused" in result.message


# -- place_draft -------------------------------------------------------------

@patch("app.core.email_client._connect")
def test_place_draft_success(mock_connect):
    mock_conn = _mock_imap([b'(\\Drafts) "/" "Drafts"'])
    mock_conn.append.return_value = ("OK", [b"1"])
    mock_connect.return_value = mock_conn

    msg = EmailMessage()
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Test"
    msg.set_content("Hello")

    result = place_draft("imap.example.com", 993, "u@x.com", "pw", msg)
    assert result.ok
    mock_conn.append.assert_called_once()


@patch("app.core.email_client._connect")
def test_place_draft_no_drafts_folder(mock_connect):
    mock_conn = _mock_imap([b'(\\HasNoChildren) "/" "INBOX"'])
    mock_conn.select.return_value = ("NO", [b"fail"])
    mock_connect.return_value = mock_conn

    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg.set_content("Hello")

    result = place_draft("imap.example.com", 993, "u@x.com", "pw", msg)
    assert not result.ok
    assert "Drafts" in result.message


# -- fetch_sent_bodies -------------------------------------------------------

@patch("app.core.email_client._connect")
def test_fetch_sent_bodies_returns_plain_text(mock_connect):
    mock_conn = _mock_imap([b'(\\Sent) "/" "Sent"'])
    mock_conn.search.return_value = ("OK", [b"1 2"])

    msg = EmailMessage()
    msg.set_content("Hello from the test.")
    raw = bytes(msg)
    mock_conn.fetch.return_value = ("OK", [(b"1 (RFC822 {100}", raw)])
    mock_connect.return_value = mock_conn

    bodies = fetch_sent_bodies("imap.example.com", 993, "u@x.com", "pw",
                               limit=5)
    assert len(bodies) >= 1
    assert "Hello from the test" in bodies[0]


@patch("app.core.email_client._connect")
def test_fetch_sent_bodies_returns_empty_on_no_sent_folder(mock_connect):
    mock_conn = _mock_imap([b'(\\HasNoChildren) "/" "INBOX"'])
    mock_conn.select.return_value = ("NO", [b"fail"])
    mock_connect.return_value = mock_conn

    bodies = fetch_sent_bodies("imap.example.com", 993, "u@x.com", "pw")
    assert bodies == []


# -- _extract_text -----------------------------------------------------------

def test_extract_text_from_plain_message():
    msg = EmailMessage()
    msg.set_content("Plain text body.")
    assert _extract_text(msg) == "Plain text body."


def test_extract_text_returns_empty_for_html_only():
    msg = EmailMessage()
    msg.set_content("<p>HTML only</p>", subtype="html")
    assert _extract_text(msg) == ""


# -- drafts.place_draft_imap (integration with drafts module) ----------------

def test_place_draft_imap_calls_through():
    from app.outreach.drafts import Draft, place_draft_imap

    draft = Draft(
        to_name="Alice",
        to_email="alice@example.com",
        subject="Hello",
        body="Testing draft placement.",
        thread_key="acme-alice",
        from_email="me@example.com",
    )
    with patch("app.core.email_client.place_draft") as mock_place:
        mock_place.return_value = IMAPResult(True, "draft placed")
        err = place_draft_imap(draft, "imap.x.com", 993, "me@x.com", "pw")
        assert err == ""
        mock_place.assert_called_once()


def test_place_draft_imap_returns_error_on_failure():
    from app.outreach.drafts import Draft, place_draft_imap

    draft = Draft(
        to_name="Bob",
        to_email="bob@example.com",
        subject="Hi",
        body="Testing.",
        thread_key="acme-bob",
    )
    with patch("app.core.email_client.place_draft") as mock_place:
        mock_place.return_value = IMAPResult(False, "no Drafts folder")
        err = place_draft_imap(draft, "imap.x.com", 993, "me@x.com", "pw")
        assert "Drafts" in err
