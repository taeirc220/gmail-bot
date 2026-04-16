"""Tests for src/main.py — orchestration and dispatch logic."""

import pytest
from unittest.mock import MagicMock, patch, call

from src.database import Database
from src.gmail_client import GmailAPIError
from src.main import (
    _dispatch,
    send_overnight_digest,
    poll_cycle,
)


# -------------------------------------------------------------------------
# Fixtures (supplement conftest.py)
# -------------------------------------------------------------------------

@pytest.fixture
def db():
    return Database(":memory:")


@pytest.fixture
def mock_notifier():
    n = MagicMock()
    n.is_quiet_hours.return_value = False
    n.send.return_value = True
    n.format_important.return_value = ("Title", "Body")
    n.format_overnight_digest.return_value = ("Digest", "2 emails")
    n.format_critical_error.return_value = ("CRITICAL", "Error")
    n.format_review_needed.return_value = ("Review", "X emails")
    n.format_bulk_paused.return_value = ("Bulk", "Paused")
    return n


@pytest.fixture
def mock_gmail():
    c = MagicMock()
    c._parse_list_unsubscribe.return_value = {"mailto": None, "http": None}
    return c


@pytest.fixture
def mock_newsletter_manager():
    return MagicMock()


@pytest.fixture
def rules():
    return {
        "personal": {
            "blocked_domains": [],
            "blocked_sender_patterns": ["noreply"],
            "personal_keywords": ["hi ", "hey "],
        },
        "tickets": {
            "subject_keywords": ["ticket"],
            "body_keywords": ["ticket"],
        },
        "job_replies": {
            "subject_keywords": ["interview"],
            "body_keywords": ["interview"],
            "blocked_headers": ["precedence"],
        },
        "newsletters": {
            "bulk_domains": ["mailchimp.com"],
        },
    }


def _email(msg_id="msg_001", **kwargs):
    base = {
        "id": msg_id,
        "thread_id": "thread_001",
        "sender": "Alice",
        "sender_email": "alice@gmail.com",
        "sender_domain": "gmail.com",
        "subject": "Hello",
        "received_at": "2024-01-01T10:00:00Z",
        "body_text": "Hey, just wanted to say hi.",
        "has_pdf": False,
        "list_unsubscribe": None,
        "raw_headers": {},
        "labels": ["INBOX"],
    }
    base.update(kwargs)
    return base


# -------------------------------------------------------------------------
# _dispatch — important
# -------------------------------------------------------------------------

class TestDispatchImportant:
    def test_important_sends_notification(self, db, mock_notifier, mock_newsletter_manager):
        buffer = []
        _dispatch(_email(), ("important", "group_a"), db, mock_notifier,
                  mock_newsletter_manager, quiet=False, overnight_buffer=buffer)
        mock_notifier.send.assert_called_once()
        assert db.is_already_processed("msg_001")

    def test_important_during_quiet_hours_is_buffered(self, db, mock_notifier, mock_newsletter_manager):
        buffer = []
        _dispatch(_email(), ("important", "group_a"), db, mock_notifier,
                  mock_newsletter_manager, quiet=True, overnight_buffer=buffer)
        mock_notifier.send.assert_not_called()
        assert len(buffer) == 1
        assert buffer[0]["sender"] == "Alice"

    def test_important_records_to_db(self, db, mock_notifier, mock_newsletter_manager):
        buffer = []
        _dispatch(_email("msg_imp"), ("important", "group_c"), db, mock_notifier,
                  mock_newsletter_manager, quiet=False, overnight_buffer=buffer)
        assert db.is_already_processed("msg_imp")


# -------------------------------------------------------------------------
# _dispatch — newsletter
# -------------------------------------------------------------------------

class TestDispatchNewsletter:
    def test_newsletter_calls_manager_handle(self, db, mock_notifier, mock_newsletter_manager):
        buffer = []
        email = _email(list_unsubscribe="<mailto:unsub@x.com>")
        _dispatch(email, ("newsletter", "high"), db, mock_notifier,
                  mock_newsletter_manager, quiet=False, overnight_buffer=buffer)
        mock_newsletter_manager.handle.assert_called_once_with(email, "high")

    def test_newsletter_low_confidence_calls_manager(self, db, mock_notifier, mock_newsletter_manager):
        buffer = []
        email = _email(list_unsubscribe="<mailto:unsub@x.com>")
        _dispatch(email, ("newsletter", "low"), db, mock_notifier,
                  mock_newsletter_manager, quiet=False, overnight_buffer=buffer)
        mock_newsletter_manager.handle.assert_called_once_with(email, "low")


# -------------------------------------------------------------------------
# _dispatch — ignored
# -------------------------------------------------------------------------

class TestDispatchIgnored:
    def test_ignored_records_to_db_no_notification(self, db, mock_notifier, mock_newsletter_manager):
        buffer = []
        _dispatch(_email("msg_ign"), ("ignored", None), db, mock_notifier,
                  mock_newsletter_manager, quiet=False, overnight_buffer=buffer)
        mock_notifier.send.assert_not_called()
        assert db.is_already_processed("msg_ign")


# -------------------------------------------------------------------------
# _dispatch — unsure
# -------------------------------------------------------------------------

class TestDispatchUnsure:
    def test_unsure_queued_to_pending_review(self, db, mock_notifier, mock_newsletter_manager):
        buffer = []
        _dispatch(_email("msg_uns"), ("unsure", "no classification"), db, mock_notifier,
                  mock_newsletter_manager, quiet=False, overnight_buffer=buffer)
        reviews = db.get_pending_reviews()
        assert len(reviews) == 1
        assert reviews[0]["message_id"] == "msg_uns"


# -------------------------------------------------------------------------
# send_overnight_digest
# -------------------------------------------------------------------------

class TestOvernightDigest:
    def test_digest_sent_and_buffer_cleared(self, mock_notifier):
        buffer = [{"sender": "Alice", "subject": "Hello"},
                  {"sender": "Bob", "subject": "World"}]
        send_overnight_digest(mock_notifier, buffer)
        mock_notifier.send.assert_called_once()
        assert buffer == []

    def test_no_digest_when_buffer_empty(self, mock_notifier):
        send_overnight_digest(mock_notifier, [])
        mock_notifier.send.assert_not_called()

    def test_digest_uses_force_true(self, mock_notifier):
        buffer = [{"sender": "Alice", "subject": "Hi"}]
        send_overnight_digest(mock_notifier, buffer)
        call_kwargs = mock_notifier.send.call_args
        assert call_kwargs.kwargs.get("force") is True


# -------------------------------------------------------------------------
# poll_cycle — first run baseline
# -------------------------------------------------------------------------

class TestPollCycleFirstRun:
    def test_first_run_sets_history_id(self, db, mock_gmail, mock_notifier, mock_newsletter_manager, rules):
        mock_gmail.get_initial_history_id.return_value = "12345"
        buffer = []

        poll_cycle(mock_gmail, db, mock_notifier, mock_newsletter_manager, rules, buffer)

        assert db.get_last_history_id() == "12345"
        mock_gmail.get_history.assert_not_called()

    def test_first_run_processes_no_emails(self, db, mock_gmail, mock_notifier, mock_newsletter_manager, rules):
        mock_gmail.get_initial_history_id.return_value = "12345"
        buffer = []

        poll_cycle(mock_gmail, db, mock_notifier, mock_newsletter_manager, rules, buffer)

        mock_gmail.get_message.assert_not_called()


# -------------------------------------------------------------------------
# poll_cycle — deduplication
# -------------------------------------------------------------------------

class TestPollCycleDeduplification:
    def test_already_processed_message_skipped(self, db, mock_gmail, mock_notifier, mock_newsletter_manager, rules):
        db.set_last_history_id("11111")
        db.record_processed("msg_dup", "x", "x", "x", "ignored", None, "none")

        mock_gmail.get_history.return_value = (["msg_dup"], "22222")
        buffer = []

        poll_cycle(mock_gmail, db, mock_notifier, mock_newsletter_manager, rules, buffer)

        mock_gmail.get_message.assert_not_called()
