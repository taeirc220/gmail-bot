"""Tests for src/database.py — uses in-memory SQLite, no disk I/O."""

import pytest
from src.database import Database, VALID_DECISIONS


@pytest.fixture
def db():
    return Database(":memory:")


# -------------------------------------------------------------------------
# emails_processed
# -------------------------------------------------------------------------

def test_record_and_check_processed(db):
    assert not db.is_already_processed("msg_001")
    db.record_processed("msg_001", "alice@example.com", "Hello", "2024-01-01T10:00:00Z",
                        "important", "group_a", "notified")
    assert db.is_already_processed("msg_001")


def test_record_processed_is_idempotent(db):
    """INSERT OR IGNORE — duplicate calls should not raise."""
    db.record_processed("msg_dup", "x@x.com", "Dup", "2024-01-01T10:00:00Z",
                        "ignored", None, "none")
    db.record_processed("msg_dup", "x@x.com", "Dup", "2024-01-01T10:00:00Z",
                        "ignored", None, "none")
    assert db.is_already_processed("msg_dup")


# -------------------------------------------------------------------------
# newsletter_decisions
# -------------------------------------------------------------------------

def test_get_sender_decision_unknown(db):
    assert db.get_sender_decision("nobody@example.com") is None


def test_record_and_get_decision(db):
    db.record_decision("news@example.com", "example.com", "unsubscribe", "auto")
    assert db.get_sender_decision("news@example.com") == "unsubscribe"


def test_record_decision_returns_latest(db):
    db.record_decision("flip@example.com", "example.com", "unsubscribe", "auto")
    db.record_decision("flip@example.com", "example.com", "keep", "user")
    assert db.get_sender_decision("flip@example.com") == "keep"


def test_record_decision_rejects_invalid(db):
    with pytest.raises(AssertionError):
        db.record_decision("x@x.com", "x.com", "INVALID_DECISION", "auto")


# -------------------------------------------------------------------------
# pending_review
# -------------------------------------------------------------------------

def test_add_and_get_pending_review(db):
    db.add_pending_review("msg_r1", "spammy@example.com", "Our weekly digest",
                          "2024-01-01T10:00:00Z", "List-Unsubscribe header present")
    rows = db.get_pending_reviews()
    assert len(rows) == 1
    assert rows[0]["message_id"] == "msg_r1"
    assert rows[0]["resolved"] == 0


def test_add_pending_review_is_idempotent(db):
    db.add_pending_review("msg_r2", "x@x.com", "Sub", "2024-01-01T10:00:00Z", "reason")
    db.add_pending_review("msg_r2", "x@x.com", "Sub", "2024-01-01T10:00:00Z", "reason")
    assert len(db.get_pending_reviews()) == 1


def test_resolve_pending_marks_resolved(db):
    db.add_pending_review("msg_r3", "news@x.com", "Weekly", "2024-01-01T10:00:00Z", "header")
    db.resolve_pending("msg_r3", "keep")
    rows = db.get_pending_reviews(unresolved_only=True)
    assert len(rows) == 0


def test_resolve_pending_records_decision(db):
    db.add_pending_review("msg_r4", "news@x.com", "Monthly", "2024-01-01T10:00:00Z", "header")
    db.resolve_pending("msg_r4", "unsubscribe")
    assert db.get_sender_decision("news@x.com") == "unsubscribe"


def test_resolve_pending_invalid_decision(db):
    db.add_pending_review("msg_r5", "x@x.com", "Sub", "2024-01-01T10:00:00Z", "reason")
    with pytest.raises(AssertionError):
        db.resolve_pending("msg_r5", "INVALID")


def test_get_actionable_reviews(db):
    db.add_pending_review("msg_r6", "x@x.com", "Sub", "2024-01-01T10:00:00Z", "reason")
    assert len(db.get_actionable_reviews()) == 0
    db.resolve_pending("msg_r6", "trash_only")
    rows = db.get_actionable_reviews()
    assert len(rows) == 1
    assert rows[0]["message_id"] == "msg_r6"


def test_mark_review_actioned(db):
    db.add_pending_review("msg_r7", "x@x.com", "Sub", "2024-01-01T10:00:00Z", "reason")
    db.resolve_pending("msg_r7", "keep")
    db.mark_review_actioned("msg_r7")
    assert len(db.get_actionable_reviews()) == 0


def test_pending_review_count(db):
    assert db.pending_review_count() == 0
    db.add_pending_review("msg_c1", "x@x.com", "A", "2024-01-01T10:00:00Z", "r")
    db.add_pending_review("msg_c2", "y@y.com", "B", "2024-01-01T10:00:00Z", "r")
    assert db.pending_review_count() == 2
    db.resolve_pending("msg_c1", "keep")
    assert db.pending_review_count() == 1


# -------------------------------------------------------------------------
# config / historyId
# -------------------------------------------------------------------------

def test_history_id_starts_none(db):
    assert db.get_last_history_id() is None


def test_set_and_get_history_id(db):
    db.set_last_history_id("12345678")
    assert db.get_last_history_id() == "12345678"


def test_update_history_id(db):
    db.set_last_history_id("111")
    db.set_last_history_id("222")
    assert db.get_last_history_id() == "222"


# -------------------------------------------------------------------------
# errors_log
# -------------------------------------------------------------------------

def test_log_error_does_not_raise(db):
    db.log_error("GmailAPIError", "HTTP 429 rate limit exceeded")
