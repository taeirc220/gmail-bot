"""Tests for src/classifier.py — verifies AND logic for all three importance groups."""

import pytest
from unittest.mock import MagicMock

from src.classifier import classify, _is_group_a, _is_group_b, _is_group_c, _classify_newsletter
from src.database import Database


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------

@pytest.fixture
def db():
    return Database(":memory:")


@pytest.fixture
def rules():
    return {
        "personal": {
            "blocked_domains": ["mailchimp.com", "sendgrid.net", "amazon.com"],
            "blocked_sender_patterns": ["noreply", "no-reply", "orders", "support", "newsletter"],
            "personal_keywords": ["i ", "you ", "hey ", "hi ", "dear ", "let me know"],
        },
        "tickets": {
            "subject_keywords": ["ticket", "booking", "reservation", "confirmation", "e-ticket"],
            "body_keywords": ["ticket", "booking reference", "seat"],
        },
        "job_replies": {
            "subject_keywords": ["application", "interview", "position", "role", "candidate"],
            "body_keywords": ["application", "interview", "next steps"],
            "blocked_headers": ["x-bulk-sender", "precedence"],
        },
        "newsletters": {
            "bulk_domains": ["mailchimp.com", "sendgrid.net"],
        },
    }


def _email(**kwargs) -> dict:
    """Construct a minimal email dict with sensible defaults."""
    base = {
        "id": "msg_001",
        "thread_id": "thread_001",
        "sender": "John Smith",
        "sender_email": "john@example.com",
        "sender_domain": "example.com",
        "subject": "Hello",
        "received_at": "2024-01-01T10:00:00Z",
        "body_text": "Hey, just wanted to let you know.",
        "has_pdf": False,
        "list_unsubscribe": None,
        "raw_headers": {},
        "labels": ["INBOX"],
    }
    base.update(kwargs)
    return base


# -------------------------------------------------------------------------
# Group A — Personal emails
# -------------------------------------------------------------------------

class TestGroupA:
    def test_all_conditions_met(self, rules, db):
        email = _email(
            sender="David Cohen",
            sender_email="david@gmail.com",
            sender_domain="gmail.com",
            body_text="Hey, just wanted to check in with you.",
            list_unsubscribe=None,
        )
        assert _is_group_a(email, rules) is True

    def test_fails_with_unsubscribe_header(self, rules, db):
        email = _email(list_unsubscribe="<mailto:unsub@example.com>")
        assert _is_group_a(email, rules) is False

    def test_fails_with_blocked_domain(self, rules, db):
        email = _email(sender_domain="mailchimp.com")
        assert _is_group_a(email, rules) is False

    def test_fails_with_blocked_sender_name(self, rules, db):
        email = _email(sender="noreply@example.com")
        assert _is_group_a(email, rules) is False

    def test_fails_without_personal_keywords(self, rules, db):
        email = _email(body_text="Your package has been shipped. Tracking number: 12345.")
        assert _is_group_a(email, rules) is False

    def test_fails_with_allcaps_sender(self, rules, db):
        email = _email(sender="AMAZON")
        assert _is_group_a(email, rules) is False

    def test_classify_returns_group_a(self, rules, db):
        email = _email(
            sender="Maria Santos",
            sender_email="maria@gmail.com",
            sender_domain="gmail.com",
            body_text="Hi, I was wondering if you could help me.",
        )
        result = classify(email, rules, db)
        assert result == ("important", "group_a")


# -------------------------------------------------------------------------
# Group B — Tickets (requires PDF)
# -------------------------------------------------------------------------

class TestGroupB:
    def _ticket_email(self, **kwargs):
        base = _email(
            subject="Your ticket confirmation",
            body_text="Please find your ticket attached. Seat 12A.",
            has_pdf=True,
            list_unsubscribe=None,
            sender_domain="ticketmaster.com",
        )
        base.update(kwargs)
        return base

    def test_all_conditions_met(self, rules, db):
        assert _is_group_b(self._ticket_email(), rules) is True

    def test_fails_without_pdf(self, rules, db):
        assert _is_group_b(self._ticket_email(has_pdf=False), rules) is False

    def test_fails_without_ticket_keyword(self, rules, db):
        email = self._ticket_email(subject="Hello", body_text="Here is your thing.")
        assert _is_group_b(email, rules) is False

    def test_fails_with_unsubscribe_header(self, rules, db):
        email = self._ticket_email(list_unsubscribe="<mailto:unsub@ticketmaster.com>")
        assert _is_group_b(email, rules) is False

    def test_fails_with_blocked_domain(self, rules, db):
        email = self._ticket_email(sender_domain="mailchimp.com")
        assert _is_group_b(email, rules) is False

    def test_group_b_beats_newsletter_in_classify(self, rules, db):
        """Ticket email with List-Unsubscribe should still be group_b (order matters)."""
        # Note: this combination is unusual but possible.
        # Group B checks list_unsubscribe=None, so this should NOT be group_b.
        # Correct: group_b requires no unsubscribe header.
        email = self._ticket_email(list_unsubscribe="<mailto:unsub@x.com>")
        result = classify(email, rules, db)
        # Since group_b fails (has unsubscribe), falls through to newsletter
        assert result[0] == "newsletter"

    def test_classify_returns_group_b(self, rules, db):
        result = classify(self._ticket_email(), rules, db)
        assert result == ("important", "group_b")


# -------------------------------------------------------------------------
# Group C — Job application replies
# -------------------------------------------------------------------------

class TestGroupC:
    def _job_email(self, **kwargs):
        base = _email(
            subject="Your application for Software Engineer",
            body_text="Thank you for your application. We would like to schedule an interview.",
            list_unsubscribe=None,
            raw_headers={},
        )
        base.update(kwargs)
        return base

    def test_all_conditions_met(self, rules, db):
        assert _is_group_c(self._job_email(), rules) is True

    def test_fails_without_job_keyword(self, rules, db):
        email = self._job_email(subject="Hello", body_text="Just checking in.")
        assert _is_group_c(email, rules) is False

    def test_fails_with_unsubscribe_header(self, rules, db):
        email = self._job_email(list_unsubscribe="<mailto:unsub@linkedin.com>")
        assert _is_group_c(email, rules) is False

    def test_fails_with_precedence_bulk_header(self, rules, db):
        email = self._job_email(raw_headers={"Precedence": "bulk"})
        assert _is_group_c(email, rules) is False

    def test_fails_with_bulk_sender_header(self, rules, db):
        email = self._job_email(raw_headers={"X-Bulk-Sender": "yes"})
        assert _is_group_c(email, rules) is False

    def test_classify_returns_group_c(self, rules, db):
        result = classify(self._job_email(), rules, db)
        assert result == ("important", "group_c")


# -------------------------------------------------------------------------
# Newsletter classification
# -------------------------------------------------------------------------

class TestNewsletter:
    def test_high_confidence_no_prior_decision(self, rules, db):
        email = _email(list_unsubscribe="<mailto:unsub@newsletter.com>",
                       sender_email="news@newsletter.com")
        result = classify(email, rules, db)
        assert result == ("newsletter", "high")

    def test_low_confidence_prior_keep(self, rules, db):
        db.record_decision("news@newsletter.com", "newsletter.com", "keep", "user")
        email = _email(list_unsubscribe="<mailto:unsub@newsletter.com>",
                       sender_email="news@newsletter.com")
        result = classify(email, rules, db)
        assert result == ("newsletter", "low")

    def test_no_unsubscribe_header_not_newsletter(self, rules, db):
        email = _email(list_unsubscribe=None)
        result = _classify_newsletter(email, db)
        assert result is None


# -------------------------------------------------------------------------
# Ignored
# -------------------------------------------------------------------------

def test_classify_ignored(rules, db):
    email = _email(
        sender="Automated System",
        sender_domain="example.com",
        body_text="Your account has been updated.",
        list_unsubscribe=None,
    )
    result = classify(email, rules, db)
    assert result == ("ignored", None)


# -------------------------------------------------------------------------
# AND logic verification — one missing condition is enough to fail each group
# -------------------------------------------------------------------------

def test_single_condition_not_enough_for_group_b(rules, db):
    """Only having a PDF (no ticket keyword) should NOT trigger group_b."""
    email = _email(has_pdf=True, subject="Hello", body_text="Just a regular email with PDF.")
    assert _is_group_b(email, rules) is False


def test_single_condition_not_enough_for_group_c(rules, db):
    """Only having 'interview' in body but with bulk header should NOT trigger group_c."""
    email = _email(
        body_text="We'd like to schedule an interview.",
        raw_headers={"Precedence": "bulk"},
    )
    assert _is_group_c(email, rules) is False


# -------------------------------------------------------------------------
# Sender rules — user-defined overrides checked before all keyword logic
# -------------------------------------------------------------------------

class TestSenderRules:
    def test_force_important_overrides_ignored(self, rules, db):
        """An email that would normally be ignored becomes important via sender rule."""
        email = _email(body_text="no keywords", list_unsubscribe=None)
        db.set_sender_rule(email["sender_email"], "force_important")
        result = classify(email, rules, db)
        assert result == ("important", "user_rule")

    def test_force_ignore_overrides_group_a(self, rules, db):
        """A personal email is ignored when a force_ignore rule exists for the sender."""
        email = _email(
            body_text="Hey, I just wanted to check in with you. Let me know how you are.",
        )
        db.set_sender_rule(email["sender_email"], "force_ignore")
        result = classify(email, rules, db)
        assert result == ("ignored", None)

    def test_force_newsletter_overrides_group_b(self, rules, db):
        """A ticket email is treated as newsletter when force_newsletter rule exists."""
        email = _email(
            subject="Your ticket confirmation",
            body_text="Please find your ticket attached. Booking reference 123.",
            has_pdf=True,
        )
        db.set_sender_rule(email["sender_email"], "force_newsletter")
        result = classify(email, rules, db)
        assert result == ("newsletter", "high")

    def test_no_rule_follows_normal_path(self, rules, db):
        """Without a sender rule, classification proceeds normally."""
        email = _email(
            body_text="Hey, I just wanted to check in with you. Let me know how you are.",
        )
        result = classify(email, rules, db)
        assert result[0] == "important"

    def test_rule_fires_before_group_b_check(self, rules, db):
        """force_ignore on a ticket email stops it before Group B logic runs."""
        email = _email(
            subject="Your e-ticket confirmation",
            body_text="Your booking reference and seat assignment are attached.",
            has_pdf=True,
        )
        db.set_sender_rule(email["sender_email"], "force_ignore")
        result = classify(email, rules, db)
        assert result == ("ignored", None)
