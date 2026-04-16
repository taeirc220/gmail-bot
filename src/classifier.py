"""
classifier.py — Email importance and newsletter classification logic.

Pure functions — no I/O, no external calls, no side effects.
All keyword lists come from importance_rules.yaml (passed as `rules` dict).
The Database is read-only here (to check prior sender decisions).
"""

import logging
import re

from src.database import Database

logger = logging.getLogger(__name__)

# Type alias for clarity
ClassificationResult = tuple[str, str | None]
# ('important', 'group_a'|'group_b'|'group_c')
# ('newsletter', 'high'|'low')
# ('unsure', reason_str)
# ('ignored', None)


def classify(email: dict, rules: dict, db: Database) -> ClassificationResult:
    """
    Classify an email using AND logic within three importance groups.

    Order: Group B (most specific) → Group C → Group A → Newsletter → Ignored.
    Reason for this order: ticket/booking emails may have List-Unsubscribe headers
    (some airlines/booking sites include them) but must still be classified as
    important tickets, not newsletters.
    """
    if _is_group_b(email, rules):
        logger.debug("Classified as important/group_b: %s", email.get("subject"))
        return ("important", "group_b")

    if _is_group_c(email, rules):
        logger.debug("Classified as important/group_c: %s", email.get("subject"))
        return ("important", "group_c")

    if _is_group_a(email, rules):
        logger.debug("Classified as important/group_a: %s", email.get("subject"))
        return ("important", "group_a")

    newsletter_result = _classify_newsletter(email, db)
    if newsletter_result is not None:
        logger.debug("Classified as %s: %s", newsletter_result, email.get("subject"))
        return newsletter_result

    logger.debug("Classified as ignored: %s", email.get("subject"))
    return ("ignored", None)


# -------------------------------------------------------------------------
# Group A — Personal emails from real humans
# -------------------------------------------------------------------------

def _is_group_a(email: dict, rules: dict) -> bool:
    """
    ALL four conditions must be true.
    1. No List-Unsubscribe header
    2. Sender domain not in blocked_domains
    3. Sender display name looks personal (not a brand/service/auto-sender)
    4. Body contains at least one personal-language keyword
    """
    personal_rules = rules.get("personal", {})

    # Condition 1: no unsubscribe header
    if email.get("list_unsubscribe"):
        return False

    # Condition 2: sender domain not bulk/commercial
    sender_domain = (email.get("sender_domain") or "").lower()
    blocked_domains = {d.lower() for d in personal_rules.get("blocked_domains", [])}
    if sender_domain in blocked_domains:
        return False

    # Condition 3: sender display name looks personal
    if not _looks_like_personal_name(email.get("sender", ""), personal_rules):
        return False

    # Condition 4: personal language in body
    body = (email.get("body_text") or "").lower()
    personal_keywords = [kw.lower() for kw in personal_rules.get("personal_keywords", [])]
    if not any(kw in body for kw in personal_keywords):
        return False

    return True


def _looks_like_personal_name(display_name: str, personal_rules: dict) -> bool:
    """
    Heuristic: a personal name is NOT any of the blocked patterns,
    is NOT all-caps (e.g. 'AMAZON'), and does NOT contain digits.
    """
    name = display_name.strip().lower()
    if not name:
        return False

    blocked_patterns = [p.lower() for p in personal_rules.get("blocked_sender_patterns", [])]
    for pattern in blocked_patterns:
        if pattern in name:
            return False

    # All-caps names (e.g. "AMAZON") suggest automated/brand senders
    if display_name.strip().isupper() and len(display_name.strip()) > 2:
        return False

    # Names with digits usually indicate auto-senders (e.g. "Support Team 24/7")
    if re.search(r"\d", display_name):
        return False

    return True


# -------------------------------------------------------------------------
# Group B — Tickets and bookings (requires PDF attachment)
# -------------------------------------------------------------------------

def _is_group_b(email: dict, rules: dict) -> bool:
    """
    ALL four conditions must be true.
    1. Subject OR body contains a ticket keyword
    2. PDF attachment present
    3. No List-Unsubscribe header
    4. Sender domain not in blocked_domains
    """
    ticket_rules = rules.get("tickets", {})
    personal_rules = rules.get("personal", {})

    # Condition 3: no unsubscribe header (checked early to short-circuit)
    if email.get("list_unsubscribe"):
        return False

    # Condition 4: not a bulk/commercial domain
    sender_domain = (email.get("sender_domain") or "").lower()
    blocked_domains = {d.lower() for d in personal_rules.get("blocked_domains", [])}
    if sender_domain in blocked_domains:
        return False

    # Condition 1: ticket keyword in subject or body
    text = " ".join([
        (email.get("subject") or ""),
        (email.get("body_text") or ""),
    ]).lower()

    subject_kws = [kw.lower() for kw in ticket_rules.get("subject_keywords", [])]
    body_kws = [kw.lower() for kw in ticket_rules.get("body_keywords", [])]
    all_kws = subject_kws + body_kws

    if not any(kw in text for kw in all_kws):
        return False

    # Condition 2: PDF attachment
    if not email.get("has_pdf"):
        return False

    return True


# -------------------------------------------------------------------------
# Group C — Job application responses
# -------------------------------------------------------------------------

def _is_group_c(email: dict, rules: dict) -> bool:
    """
    ALL three conditions must be true.
    1. Subject OR body contains a job keyword
    2. No List-Unsubscribe header
    3. No bulk headers present (Precedence: bulk, X-Bulk-*)
    """
    job_rules = rules.get("job_replies", {})

    # Condition 2: no unsubscribe header
    if email.get("list_unsubscribe"):
        return False

    # Condition 3: no bulk mail headers
    blocked_headers = [h.lower() for h in job_rules.get("blocked_headers", [])]
    raw_headers = {k.lower(): v for k, v in (email.get("raw_headers") or {}).items()}

    for blocked in blocked_headers:
        if blocked in raw_headers:
            val = raw_headers[blocked].lower()
            if blocked == "precedence" and "bulk" in val:
                return False
            elif blocked != "precedence":
                return False

    # Condition 1: job keyword in subject or body
    text = " ".join([
        (email.get("subject") or ""),
        (email.get("body_text") or ""),
    ]).lower()

    subject_kws = [kw.lower() for kw in job_rules.get("subject_keywords", [])]
    body_kws = [kw.lower() for kw in job_rules.get("body_keywords", [])]
    all_kws = subject_kws + body_kws

    if not any(kw in text for kw in all_kws):
        return False

    return True


# -------------------------------------------------------------------------
# Newsletter classification
# -------------------------------------------------------------------------

def _classify_newsletter(email: dict, db: Database) -> ClassificationResult | None:
    """
    Returns a newsletter result if the email looks like a newsletter, else None.

    Confidence:
    - High: List-Unsubscribe header present AND no prior 'keep' decision for sender
    - Low:  List-Unsubscribe present BUT sender has a prior 'keep' decision
    """
    if not email.get("list_unsubscribe"):
        return None

    sender_email = email.get("sender_email", "")
    prior_decision = db.get_sender_decision(sender_email)

    if prior_decision == "keep":
        return ("newsletter", "low")

    return ("newsletter", "high")
