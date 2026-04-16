"""Tests for src/notifier.py."""

import pytest
from unittest.mock import MagicMock, patch, call
from src.notifier import Notifier, NotifierError, _five_word_summary


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------

@pytest.fixture
def notifier():
    """Notifier with mocked win10toast so no real notifications fire."""
    with patch("src.notifier.Notifier._load_toast", return_value=MagicMock()):
        n = Notifier(quiet_hours_start=22, quiet_hours_end=7)
    return n


# -------------------------------------------------------------------------
# Quiet hours logic
# -------------------------------------------------------------------------

class TestQuietHours:
    def test_in_quiet_hours_at_23(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23
            assert notifier.is_quiet_hours() is True

    def test_in_quiet_hours_at_midnight(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 0
            assert notifier.is_quiet_hours() is True

    def test_in_quiet_hours_at_6(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 6
            assert notifier.is_quiet_hours() is True

    def test_not_quiet_at_7(self, notifier):
        """7:00 is the end boundary — should NOT be quiet."""
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 7
            assert notifier.is_quiet_hours() is False

    def test_not_quiet_at_noon(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            assert notifier.is_quiet_hours() is False

    def test_not_quiet_at_21(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 21
            assert notifier.is_quiet_hours() is False

    def test_in_quiet_hours_at_22(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 22
            assert notifier.is_quiet_hours() is True


# -------------------------------------------------------------------------
# send() — suppression and dispatch
# -------------------------------------------------------------------------

class TestSend:
    def test_send_suppressed_during_quiet_hours(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23
            result = notifier.send("Title", "Body")
        assert result is False
        notifier._toast.show_toast.assert_not_called()

    def test_send_dispatched_outside_quiet_hours(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 10
            result = notifier.send("Title", "Body")
        assert result is True
        notifier._toast.show_toast.assert_called_once()

    def test_force_bypasses_quiet_hours(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23
            result = notifier.send("Title", "Body", force=True)
        assert result is True
        notifier._toast.show_toast.assert_called_once()

    def test_toast_called_with_correct_title(self, notifier):
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 10
            notifier.send("My Title", "My Body")
        call_kwargs = notifier._toast.show_toast.call_args
        assert call_kwargs.kwargs.get("title") == "My Title" or call_kwargs.args[0] == "My Title"

    def test_send_raises_notifier_error_on_failure(self, notifier):
        notifier._toast.show_toast.side_effect = RuntimeError("Toast failed")
        with patch("src.notifier.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 10
            with pytest.raises(NotifierError, match="Failed to send notification"):
                notifier.send("Title", "Body")


# -------------------------------------------------------------------------
# Formatters
# -------------------------------------------------------------------------

class TestFormatters:
    def test_format_important_group_b(self, notifier):
        title, body = notifier.format_important("group_b", "Ticketmaster", "msg_001", "Your ticket")
        assert "Ticket" in title
        assert "Ticketmaster" in title

    def test_format_important_group_c(self, notifier):
        title, body = notifier.format_important("group_c", "Google Recruiting", "msg_002",
                                                 "Your application for SWE position")
        assert "Job alert" in title
        assert "Google Recruiting" in title

    def test_format_important_group_a(self, notifier):
        title, body = notifier.format_important("group_a", "David Cohen", "msg_003",
                                                 subject="Weekend hiking plans",
                                                 summary="Hey, want to join us?")
        assert title == "David Cohen"
        assert "Hey" in body

    def test_format_review_needed_singular(self, notifier):
        title, body = notifier.format_review_needed(1)
        assert "1" in body
        assert "newsletter" in body.lower()

    def test_format_review_needed_plural(self, notifier):
        title, body = notifier.format_review_needed(3)
        assert "3" in body
        assert "newsletters" in body.lower()

    def test_format_auth_failure(self, notifier):
        title, body = notifier.format_auth_failure()
        assert "Auth" in title
        assert "Re-authentication" in body

    def test_format_overnight_digest(self, notifier):
        title, body = notifier.format_overnight_digest(5)
        assert "5" in body

    def test_format_critical_error(self, notifier):
        title, body = notifier.format_critical_error("GmailAPIError")
        assert "CRITICAL" in title
        assert "GmailAPIError" in body


# -------------------------------------------------------------------------
# _five_word_summary helper
# -------------------------------------------------------------------------

class TestFiveWordSummary:
    def test_returns_up_to_five_words(self):
        result = _five_word_summary("This is a long sentence with many words")
        assert result == "This is a long sentence"

    def test_returns_all_words_if_fewer_than_five(self):
        result = _five_word_summary("Hello there")
        assert result == "Hello there"

    def test_empty_string_returns_default(self):
        result = _five_word_summary("")
        assert result == "New personal email"

    def test_none_like_empty_returns_default(self):
        result = _five_word_summary("   ")
        assert result == "New personal email"
