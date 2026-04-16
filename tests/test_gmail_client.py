"""Tests for src/gmail_client.py — mocks the Gmail API service."""

import base64
import json
import pytest
from unittest.mock import MagicMock, patch

from src.gmail_client import GmailClient, GmailAPIError


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------

@pytest.fixture
def mock_service():
    return MagicMock()


@pytest.fixture
def client(mock_service):
    with patch("src.gmail_client.build", return_value=mock_service):
        return GmailClient(credentials=MagicMock())


# -------------------------------------------------------------------------
# _parse_sender
# -------------------------------------------------------------------------

def test_parse_sender_name_and_email(client):
    name, email, domain = client._parse_sender("John Smith <john@example.com>")
    assert name == "John Smith"
    assert email == "john@example.com"
    assert domain == "example.com"


def test_parse_sender_quoted_name(client):
    name, email, domain = client._parse_sender('"Amazon Orders" <orders@amazon.com>')
    assert name == "Amazon Orders"
    assert email == "orders@amazon.com"


def test_parse_sender_bare_email(client):
    name, email, domain = client._parse_sender("alice@example.com")
    assert email == "alice@example.com"
    assert domain == "example.com"


def test_parse_sender_no_domain(client):
    name, email, domain = client._parse_sender("")
    assert domain == ""


# -------------------------------------------------------------------------
# _parse_list_unsubscribe
# -------------------------------------------------------------------------

def test_parse_unsubscribe_mailto_and_http(client):
    header = "<mailto:unsub@example.com>, <https://example.com/unsub>"
    result = client._parse_list_unsubscribe(header)
    assert result["mailto"] == "unsub@example.com"
    assert result["http"] == "https://example.com/unsub"


def test_parse_unsubscribe_mailto_only(client):
    result = client._parse_list_unsubscribe("<mailto:unsub@example.com>")
    assert result["mailto"] == "unsub@example.com"
    assert result["http"] is None


def test_parse_unsubscribe_http_only(client):
    result = client._parse_list_unsubscribe("<https://example.com/unsub>")
    assert result["mailto"] is None
    assert result["http"] == "https://example.com/unsub"


def test_parse_unsubscribe_none(client):
    result = client._parse_list_unsubscribe(None)
    assert result["mailto"] is None
    assert result["http"] is None


def test_parse_unsubscribe_empty(client):
    result = client._parse_list_unsubscribe("")
    assert result["mailto"] is None
    assert result["http"] is None


# -------------------------------------------------------------------------
# _decode_body
# -------------------------------------------------------------------------

def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_decode_body_plain_text(client):
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("Hello, this is a plain text email.")},
        "parts": [],
    }
    result = client._decode_body(payload)
    assert "Hello" in result


def test_decode_body_html_fallback(client):
    payload = {
        "mimeType": "text/html",
        "body": {"data": _b64("<p>Hello from <b>HTML</b></p>")},
        "parts": [],
    }
    result = client._decode_body(payload)
    assert "Hello" in result
    assert "<p>" not in result


def test_decode_body_prefers_plain_over_html(client):
    plain_payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("Plain text version")},
        "parts": [],
    }
    html_payload = {
        "mimeType": "text/html",
        "body": {"data": _b64("<p>HTML version</p>")},
        "parts": [],
    }
    multipart = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [plain_payload, html_payload],
    }
    result = client._decode_body(multipart)
    assert "Plain text version" in result
    assert "HTML version" not in result


def test_decode_body_empty_returns_empty_string(client):
    payload = {"mimeType": "multipart/mixed", "body": {}, "parts": []}
    result = client._decode_body(payload)
    assert result == ""


# -------------------------------------------------------------------------
# _has_pdf_attachment
# -------------------------------------------------------------------------

def test_has_pdf_attachment_true(client):
    payload = {
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "application/pdf",
                "filename": "ticket.pdf",
                "body": {"attachmentId": "att_001", "size": 12345},
                "parts": [],
            }
        ],
    }
    assert client._has_pdf_attachment(payload) is True


def test_has_pdf_attachment_false(client):
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("No attachments here")},
        "parts": [],
    }
    assert client._has_pdf_attachment(payload) is False


def test_has_pdf_attachment_by_filename(client):
    payload = {
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "application/octet-stream",
                "filename": "document.pdf",
                "body": {"attachmentId": "att_002", "size": 5000},
                "parts": [],
            }
        ],
    }
    assert client._has_pdf_attachment(payload) is True


# -------------------------------------------------------------------------
# get_history
# -------------------------------------------------------------------------

def test_get_history_returns_new_ids(client, mock_service):
    mock_service.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "historyId": "99999",
        "history": [
            {"messagesAdded": [{"message": {"id": "msg_new_1"}}]},
            {"messagesAdded": [{"message": {"id": "msg_new_2"}}]},
        ],
    }

    ids, new_history_id = client.get_history("11111")
    assert ids == ["msg_new_1", "msg_new_2"]
    assert new_history_id == "99999"


def test_get_history_empty(client, mock_service):
    mock_service.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "historyId": "11111",
    }

    ids, new_history_id = client.get_history("11111")
    assert ids == []
    assert new_history_id == "11111"


def test_get_history_deduplicates_ids(client, mock_service):
    mock_service.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "historyId": "99999",
        "history": [
            {"messagesAdded": [{"message": {"id": "msg_dup"}}]},
            {"messagesAdded": [{"message": {"id": "msg_dup"}}]},
        ],
    }

    ids, _ = client.get_history("11111")
    assert ids == ["msg_dup"]


# -------------------------------------------------------------------------
# get_message — minimal smoke test
# -------------------------------------------------------------------------

def test_get_message_returns_email_dict(client, mock_service):
    internal_date = "1704067200000"  # 2024-01-01T00:00:00Z in ms

    mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "id": "msg_001",
        "threadId": "thread_001",
        "labelIds": ["INBOX"],
        "internalDate": internal_date,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Subject", "value": "Hello there"},
            ],
            "body": {"data": _b64("This is a test email body.")},
            "parts": [],
        },
    }

    result = client.get_message("msg_001")

    assert result["id"] == "msg_001"
    assert result["sender"] == "Alice"
    assert result["sender_email"] == "alice@example.com"
    assert result["sender_domain"] == "example.com"
    assert result["subject"] == "Hello there"
    assert "test email body" in result["body_text"]
    assert result["has_pdf"] is False
    assert result["list_unsubscribe"] is None
    assert result["labels"] == ["INBOX"]
