"""Tests for safety rails: bulk threshold and DRY_RUN enforcement."""

import pytest
from unittest.mock import MagicMock, patch

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
    c = MagicMock()
    c._parse_list_unsubscribe.return_value = {"http": None, "mailto": None}
    return c


@pytest.fixture
def mock_notifier():
    n = MagicMock()
    n.format_bulk_paused.return_value = ("Bulk pause", "Paused")
    n.format_review_needed.return_value = ("Review", "X need review")
    n.is_quiet_hours.return_value = False
    return n


@pytest.fixture
def empty_whitelist(tmp_path):
    wl = tmp_path / "whitelist.txt"
    wl.write_text("")
    return str(wl)


def _make_manager(db, gmail, notifier, whitelist, dry_run=False):
    return NewsletterManager(
        gmail_client=gmail,
        db=db,
        notifier=notifier,
        whitelist_path=whitelist,
        dry_run=dry_run,
    )


def _email(msg_id: str) -> dict:
    return {
        "id": msg_id,
        "sender": "Newsletter",
        "sender_email": f"news{msg_id}@newsletter.com",
        "sender_domain": "newsletter.com",
        "subject": f"Newsletter {msg_id}",
        "received_at": "2024-01-01T10:00:00Z",
        "list_unsubscribe": "<https://newsletter.com/unsub>",
        "raw_headers": {},
    }


# -------------------------------------------------------------------------
# Bulk deletion threshold
# -------------------------------------------------------------------------

class TestBulkThreshold:
    def test_first_ten_deletions_proceed(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.reset_cycle_counter()

        for i in range(BULK_LIMIT):
            result = mgr.handle(_email(f"msg_{i:03d}"), "high")
            assert result == "unsubscribed_and_trashed", f"Expected success on deletion {i+1}"

        assert mock_gmail.trash_message.call_count == BULK_LIMIT

    def test_eleventh_deletion_is_paused(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.reset_cycle_counter()

        # Process exactly BULK_LIMIT emails first
        for i in range(BULK_LIMIT):
            mgr.handle(_email(f"msg_{i:03d}"), "high")

        # 11th should be paused
        result = mgr.handle(_email("msg_011"), "high")
        assert result == "paused_bulk_limit"

    def test_pause_sends_notification(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.reset_cycle_counter()

        for i in range(BULK_LIMIT):
            mgr.handle(_email(f"msg_{i:03d}"), "high")

        mgr.handle(_email("msg_011"), "high")
        mock_notifier.send.assert_called()

    def test_counter_resets_between_cycles(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)

        # Cycle 1: hit the limit
        mgr.reset_cycle_counter()
        for i in range(BULK_LIMIT):
            mgr.handle(_email(f"c1_{i:03d}"), "high")
        paused = mgr.handle(_email("c1_overflow"), "high")
        assert paused == "paused_bulk_limit"

        # Cycle 2: counter resets, new emails proceed
        mgr.reset_cycle_counter()
        result = mgr.handle(_email("c2_first"), "high")
        assert result == "unsubscribed_and_trashed"

    def test_trash_not_called_when_paused(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist)
        mgr.reset_cycle_counter()

        for i in range(BULK_LIMIT):
            mgr.handle(_email(f"msg_{i:03d}"), "high")

        trash_calls_before = mock_gmail.trash_message.call_count
        mgr.handle(_email("overflow"), "high")
        # No additional trash calls after limit is hit
        assert mock_gmail.trash_message.call_count == trash_calls_before


# -------------------------------------------------------------------------
# DRY_RUN — no Gmail API writes
# -------------------------------------------------------------------------

class TestDryRunEnforcement:
    def test_dry_run_no_trash_calls(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist, dry_run=True)
        for i in range(5):
            mgr.handle(_email(f"msg_{i}"), "high")
        mock_gmail.trash_message.assert_not_called()

    def test_dry_run_no_send_email_calls(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist, dry_run=True)
        mgr.handle(_email("msg_001"), "high")
        mock_gmail.send_email.assert_not_called()

    def test_dry_run_still_records_to_db(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist, dry_run=True)
        mgr.handle(_email("msg_dry_001"), "high")
        assert db.is_already_processed("msg_dry_001")

    def test_dry_run_does_not_increment_deletion_count(self, db, mock_gmail, mock_notifier, empty_whitelist):
        mgr = _make_manager(db, mock_gmail, mock_notifier, empty_whitelist, dry_run=True)
        mgr.reset_cycle_counter()
        for i in range(BULK_LIMIT + 5):
            result = mgr.handle(_email(f"msg_{i}"), "high")
            assert result == "dry_run"


# -------------------------------------------------------------------------
# Whitelist always wins over everything
# -------------------------------------------------------------------------

class TestWhitelistPrecedence:
    def test_whitelist_wins_over_high_confidence(self, db, mock_gmail, mock_notifier, tmp_path):
        wl = tmp_path / "whitelist.txt"
        wl.write_text("newsmsg_001@newsletter.com\n")
        mgr = _make_manager(db, mock_gmail, mock_notifier, str(wl))
        result = mgr.handle(_email("msg_001"), "high")
        assert result == "skipped_whitelist"
        mock_gmail.trash_message.assert_not_called()

    def test_whitelist_wins_over_bulk_limit(self, db, mock_gmail, mock_notifier, tmp_path):
        wl = tmp_path / "whitelist.txt"
        wl.write_text("@newsletter.com\n")
        mgr = _make_manager(db, mock_gmail, mock_notifier, str(wl))
        mgr.reset_cycle_counter()

        # Manually force bulk limit to simulate already-reached state
        mgr._deletion_count = BULK_LIMIT + 1

        result = mgr.handle(_email("wl_msg"), "high")
        assert result == "skipped_whitelist"
