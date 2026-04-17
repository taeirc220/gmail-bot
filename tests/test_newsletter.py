"""Tests for src/newsletter_manager.py — newsletter handling logic."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import src.bot_state as bot_state
from src.newsletter_manager import NewsletterManager, BULK_LIMIT
from src.database import Database


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------

@pytest.fixture
def db():
    return Database(":memory:")


@pytest.fixture
def mock_gmail():
    return MagicMock()


@pytest.fixture
def mock_notifier():
    n = MagicMock()
    n.format_review_needed.return_value = ("Review needed", "1 newsletter needs your review")
    n.format_bulk_paused.return_value = ("Bulk pause", "Paused")
    n.is_quiet_hours.return_value = False
    return n


@pytest.fixture
def whitelist_file(tmp_path):
    wl = tmp_path / "whitelist.txt"
    wl.write_text("# whitelist\nkept@example.com\n@trusted.com\n")
    return str(wl)


@pytest.fixture
def empty_whitelist(tmp_path):
    wl = tmp_path / "whitelist.txt"
    wl.write_text("")
    return str(wl)


def _make_manager(db, mock_gmail, mock_notifier, whitelist_path, dry_run=False):
    bot_state.set_dry_run(dry_run)
    return NewsletterManager(
        gmail_client=mock_gmail,
        db=db,
        notifier=mock_notifier,
        whitelist_path=whitelist_path,
        dry_run=dry_run,
    )


def _newsletter_email(**kwargs):
    base = {
        "id": "msg_nl_001",
        "sender": "Newsletter Co",
        "sender_email": "news@newsletter.com",
        "sender_domain": "newsletter.com",
        "subject": "Weekly digest",
        "received_at": "2024-01-01T10:00:00Z",
        "list_unsubscribe": "<https://newsletter.com/unsub>",
        "raw_headers": {},
    }
    base.update(kwargs)
    return base


# -------------------------------------------------------------------------
# Whitelist
# -------------------------------------------------------------------------

class TestWhitelist:
    def test_exact_email_whitelisted(self, db, mock_gmail, mock_notifier, whitelist_file):
        mgr = _make_manager(db, mock_gmail, mock_notifier, whitelist_file)
        email = _newsletter_email(sender_email="kept@example.com", sender_domain="example.com")
        result = mgr.handle(email, "high")
        assert result == "skipped_whitelist"
        mock_gmail.trash_message.assert_not_called()

    def test_domain_whitelisted(self, db, mock_gmail, mock_notifier, whitelist_file):
        mgr = _make_manager(db, mock_gmail, mock_notifier, whitelist_file)
        email = _newsletter_email(sender_email="any@trusted.com", sender_domain="trusted.com")
        result = mgr.handle(email, "high")
        assert result == "skipped_whitelist"

    def test_non_whitelisted_proceeds(self, db, mock_gmail, mock_notifier, whitelist_file):
        mock_gmail._parse_list_unsubscribe.return_value = {"http": "https://x.com/unsub", "mailto": None}
        mgr = _make_manager(db, mock_gmail, mock_notifier, whitelist_file)
        email = _newsletter_email()
        result = mgr.handle(email, "high")
        assert result == "unsubscribed_and_trashed"


# -------------------------------------------------------------------------
# DRY RUN
# -------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_logs_but_no_trash(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist, dry_run=True)
        result = mgr.handle(_newsletter_email(), "high")
        assert result == "dry_run"
        mock_gmail.trash_message.assert_not_called()
        mock_gmail.send_email.assert_not_called()

    def test_dry_run_records_to_db(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist, dry_run=True)
        mgr.handle(_newsletter_email(), "high")
        assert db.is_already_processed("msg_nl_001")

    def test_dry_run_skips_low_confidence_too(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist, dry_run=True)
        # Low confidence goes to review queue before dry_run check
        result = mgr.handle(_newsletter_email(), "low")
        assert result == "queued_for_review"


# -------------------------------------------------------------------------
# High confidence — unsubscribe + trash
# -------------------------------------------------------------------------

class TestHighConfidence:
    def test_high_confidence_calls_trash(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mock_gmail._parse_list_unsubscribe.return_value = {"http": None, "mailto": None}
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.handle(_newsletter_email(), "high")
        mock_gmail.trash_message.assert_called_once_with("msg_nl_001")

    def test_high_confidence_records_action(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mock_gmail._parse_list_unsubscribe.return_value = {"http": None, "mailto": None}
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.handle(_newsletter_email(), "high")
        assert db.is_already_processed("msg_nl_001")

    def test_http_unsubscribe_preferred_over_mailto(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mock_gmail._parse_list_unsubscribe.return_value = {
            "http": "https://x.com/unsub",
            "mailto": "unsub@x.com",
        }
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        with patch.object(mgr, "_unsubscribe_http") as mock_http, \
             patch.object(mgr, "_unsubscribe_mailto") as mock_mailto:
            method = mgr.unsubscribe(_newsletter_email())
        assert method == "http"
        mock_http.assert_called_once()
        mock_mailto.assert_not_called()


# -------------------------------------------------------------------------
# Low confidence — queue for review
# -------------------------------------------------------------------------

class TestLowConfidence:
    def test_low_confidence_does_not_trash(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        result = mgr.handle(_newsletter_email(), "low")
        assert result == "queued_for_review"
        mock_gmail.trash_message.assert_not_called()

    def test_low_confidence_adds_pending_review(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.handle(_newsletter_email(), "low")
        reviews = db.get_pending_reviews()
        assert len(reviews) == 1
        assert reviews[0]["message_id"] == "msg_nl_001"

    def test_low_confidence_sends_review_notification(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.handle(_newsletter_email(), "low")
        mock_notifier.send.assert_called()
