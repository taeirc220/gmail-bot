"""
conftest.py — Shared pytest fixtures for the Gmail Automation Bot test suite.

All tests run with DRY_RUN=true and TEST_MODE=true enforced.
No real Gmail API writes are made during any test run.
"""

import json
import os
import pytest
from unittest.mock import MagicMock

from src.database import Database
from src.gmail_client import GmailClient
from src.notifier import Notifier


# -------------------------------------------------------------------------
# Safety: enforce DRY_RUN and TEST_MODE for every test
# -------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def enforce_test_mode(monkeypatch):
    """
    Applied to every test automatically.
    Ensures no test can accidentally write to the real Gmail inbox.
    """
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("TEST_MODE", "true")


# -------------------------------------------------------------------------
# Database
# -------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """In-memory SQLite database, fresh for each test."""
    return Database(":memory:")


# -------------------------------------------------------------------------
# Sample email dict (the canonical data contract)
# -------------------------------------------------------------------------

@pytest.fixture
def sample_email():
    """
    A minimal, valid personal email dict as returned by GmailClient.get_message().
    Satisfies Group A conditions by default.
    Override individual fields as needed in specific tests.
    """
    return {
        "id": "msg_sample_001",
        "thread_id": "thread_sample_001",
        "sender": "David Cohen",
        "sender_email": "david@gmail.com",
        "sender_domain": "gmail.com",
        "subject": "Hey, checking in",
        "received_at": "2024-04-01T10:00:00Z",
        "body_text": "Hey, I just wanted to check in with you. Let me know how you are.",
        "has_pdf": False,
        "list_unsubscribe": None,
        "raw_headers": {},
        "labels": ["INBOX"],
    }


@pytest.fixture
def ticket_email():
    """A valid ticket/booking email dict that satisfies Group B conditions."""
    return {
        "id": "msg_ticket_001",
        "thread_id": "thread_ticket_001",
        "sender": "Ticketmaster",
        "sender_email": "tickets@ticketmaster.com",
        "sender_domain": "ticketmaster.com",
        "subject": "Your ticket confirmation — Match Day",
        "received_at": "2024-04-01T12:00:00Z",
        "body_text": "Please find your ticket attached. Seat 12A, Row B.",
        "has_pdf": True,
        "list_unsubscribe": None,
        "raw_headers": {},
        "labels": ["INBOX"],
    }


@pytest.fixture
def job_email():
    """A valid job application response email dict that satisfies Group C conditions."""
    return {
        "id": "msg_job_001",
        "thread_id": "thread_job_001",
        "sender": "Google Recruiting",
        "sender_email": "recruiting@google.com",
        "sender_domain": "google.com",
        "subject": "Your application for Software Engineer",
        "received_at": "2024-04-01T14:00:00Z",
        "body_text": (
            "Thank you for your application. We would like to schedule an interview "
            "with you for the Software Engineer position. Please let us know your availability."
        ),
        "has_pdf": False,
        "list_unsubscribe": None,
        "raw_headers": {},
        "labels": ["INBOX"],
    }


@pytest.fixture
def newsletter_email():
    """A high-confidence newsletter email dict."""
    return {
        "id": "msg_news_001",
        "thread_id": "thread_news_001",
        "sender": "The Weekly Digest",
        "sender_email": "digest@newsletter.example.com",
        "sender_domain": "newsletter.example.com",
        "subject": "Your weekly digest — Issue #42",
        "received_at": "2024-04-01T08:00:00Z",
        "body_text": "Here is your weekly roundup of news...",
        "has_pdf": False,
        "list_unsubscribe": "<mailto:unsub@newsletter.example.com>, <https://newsletter.example.com/unsub>",
        "raw_headers": {},
        "labels": ["INBOX"],
    }


# -------------------------------------------------------------------------
# Mocked Gmail client (no real API calls)
# -------------------------------------------------------------------------

@pytest.fixture
def mock_gmail_client():
    """Mock GmailClient — all methods are MagicMocks."""
    client = MagicMock(spec=GmailClient)
    client._parse_list_unsubscribe.return_value = {"mailto": None, "http": None}
    return client


# -------------------------------------------------------------------------
# Mocked Notifier (no real Windows toasts)
# -------------------------------------------------------------------------

@pytest.fixture
def mock_notifier():
    """Mock Notifier — no real desktop notifications fired."""
    notifier = MagicMock(spec=Notifier)
    notifier.is_quiet_hours.return_value = False
    notifier.send.return_value = True
    notifier.format_review_needed.return_value = ("Review needed", "X newsletters need review")
    notifier.format_auth_failure.return_value = ("Auth Error", "Re-authentication required")
    notifier.format_overnight_digest.return_value = ("Overnight", "X emails while away")
    notifier.format_critical_error.return_value = ("CRITICAL", "Error")
    notifier.format_bulk_paused.return_value = ("Bulk pause", "Paused")
    return notifier


# -------------------------------------------------------------------------
# Importance rules (mirrors the structure of config/importance_rules.yaml)
# -------------------------------------------------------------------------

@pytest.fixture
def rules():
    """Minimal importance rules dict for classifier tests."""
    return {
        "personal": {
            "blocked_domains": ["mailchimp.com", "sendgrid.net", "amazon.com"],
            "blocked_sender_patterns": [
                "noreply", "no-reply", "orders", "support", "newsletter",
                "billing", "alerts", "notifications",
            ],
            "personal_keywords": [
                "i ", "i'm", "you ", "your ", "hey ", "hi ",
                "dear ", "let me know", "wanted to", "checking in",
            ],
        },
        "tickets": {
            "subject_keywords": [
                "ticket", "e-ticket", "booking", "reservation",
                "confirmation", "boarding pass", "seat", "admission",
            ],
            "body_keywords": [
                "ticket", "booking reference", "seat", "boarding pass",
            ],
            "attachment_mime_types": ["application/pdf"],
        },
        "job_replies": {
            "subject_keywords": [
                "application", "interview", "position", "vacancy",
                "role", "hiring", "recruiter", "candidate",
                "thank you for applying", "next steps",
            ],
            "body_keywords": [
                "application", "interview", "next steps",
                "thank you for your interest", "position",
            ],
            "blocked_headers": ["x-bulk-sender", "precedence"],
        },
        "newsletters": {
            "bulk_domains": ["mailchimp.com", "sendgrid.net", "substack.com"],
        },
    }
