"""
newsletter_manager.py — Newsletter unsubscribe, trash, and safety rail logic.

This is the highest-risk module — it contains all code that can permanently
(within Trash 30-day window) delete emails. All safety rails live here:
  - Sender whitelist (never touch these)
  - DRY_RUN mode (log only, no API writes)
  - Bulk deletion threshold (pause if >10 in one cycle)
  - Trash only (no hard-delete in v1)
"""

import logging
import re
from pathlib import Path

import requests as http_requests

import src.bot_state as bot_state
from src.database import Database
from src.gmail_client import GmailClient, GmailAPIError
from src.notifier import Notifier

logger = logging.getLogger(__name__)

BULK_LIMIT = 10


class NewsletterManager:
    def __init__(
        self,
        gmail_client: GmailClient,
        db: Database,
        notifier: Notifier,
        whitelist_path: str,
        dry_run: bool = False,
    ) -> None:
        self._client = gmail_client
        self._db = db
        self._notifier = notifier
        self._whitelist = self._load_whitelist(whitelist_path)
        self._deletion_count = 0
        logger.info(
            "NewsletterManager initialised. dry_run=%s, whitelist entries=%d",
            dry_run,
            len(self._whitelist),
        )

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def reset_cycle_counter(self) -> None:
        """Must be called by main.py at the start of each poll cycle."""
        self._deletion_count = 0

    def handle(self, email: dict, confidence: str) -> str:
        """
        Process a classified newsletter email.

        Returns one of:
          'skipped_whitelist'       — sender is on the whitelist
          'dry_run'                 — DRY_RUN mode, no real action taken
          'paused_bulk_limit'       — >10 deletions this cycle
          'unsubscribed_and_trashed'— high confidence, action complete
          'queued_for_review'       — low confidence, added to pending_review
        """
        # 1. Whitelist check — always first, overrides everything
        if self._is_whitelisted(email):
            logger.info("Skipped (whitelist): %s", email.get("sender_email"))
            self._db.record_processed(
                email["id"], email.get("sender", ""), email.get("subject", ""),
                email.get("received_at", ""), "newsletter", confidence,
                "skipped_whitelist",
            )
            return "skipped_whitelist"

        # 2. Low confidence → queue for user review
        if confidence == "low":
            return self._queue_for_review(email)

        # 3. DRY_RUN mode — log, record, do not touch Gmail
        if bot_state.get_dry_run():
            logger.info(
                "[DRY RUN] Would have unsubscribed and trashed: %s | %s",
                email.get("sender_email"),
                email.get("subject"),
            )
            self._db.record_processed(
                email["id"], email.get("sender", ""), email.get("subject", ""),
                email.get("received_at", ""), "newsletter", confidence,
                "dry_run_would_trash",
            )
            return "dry_run"

        # 4. Bulk limit check
        if self._check_bulk_limit():
            logger.warning(
                "Bulk limit reached (%d deletions this cycle). Pausing.", self._deletion_count
            )
            title, body = self._notifier.format_bulk_paused(self._deletion_count)
            try:
                self._notifier.send(title, body, force=True)
            except Exception:
                pass
            self._db.log_error(
                "BULK_LIMIT_HIT",
                f"Bulk limit hit at {self._deletion_count} deletions this cycle",
            )
            return "paused_bulk_limit"

        # 5. High confidence — unsubscribe + trash
        return self._unsubscribe_and_trash(email, confidence)

    # -------------------------------------------------------------------------
    # Internal actions
    # -------------------------------------------------------------------------

    def _unsubscribe_and_trash(self, email: dict, confidence: str) -> str:
        method = self.unsubscribe(email)
        logger.info(
            "Unsubscribed (method=%s) from %s", method, email.get("sender_email")
        )

        try:
            self._client.trash_message(email["id"])
        except GmailAPIError as exc:
            logger.error("Failed to trash %s: %s", email["id"], exc)
            self._db.log_error("TRASH_FAILED", str(exc))
            raise

        self._deletion_count += 1
        self._db.record_processed(
            email["id"], email.get("sender", ""), email.get("subject", ""),
            email.get("received_at", ""), "newsletter", confidence,
            "unsubscribed_and_trashed",
        )
        logger.info("Trashed newsletter: %s | %s", email.get("sender_email"), email.get("subject"))
        return "unsubscribed_and_trashed"

    def _queue_for_review(self, email: dict) -> str:
        self._db.add_pending_review(
            message_id=email["id"],
            sender=email.get("sender_email", ""),
            subject=email.get("subject", ""),
            received_at=email.get("received_at", ""),
            flag_reason="List-Unsubscribe header present but sender has prior 'keep' decision",
        )
        self._db.record_processed(
            email["id"], email.get("sender", ""), email.get("subject", ""),
            email.get("received_at", ""), "newsletter", "low",
            "queued",
        )
        logger.info("Queued for review: %s | %s", email.get("sender_email"), email.get("subject"))

        count = self._db.pending_review_count()
        title, body = self._notifier.format_review_needed(count)
        try:
            self._notifier.send(title, body)
        except Exception:
            pass

        return "queued_for_review"

    # -------------------------------------------------------------------------
    # Unsubscribe
    # -------------------------------------------------------------------------

    def unsubscribe(self, email: dict) -> str:
        """
        Attempt to unsubscribe using the List-Unsubscribe header.
        Prefers HTTP over mailto (more reliable).
        Returns: 'http' | 'mailto' | 'none'
        """
        header_value = email.get("list_unsubscribe")
        if not header_value:
            return "none"

        parsed = self._client._parse_list_unsubscribe(header_value)
        http_url = parsed.get("http")
        mailto_addr = parsed.get("mailto")

        if http_url:
            try:
                self._unsubscribe_http(http_url)
                return "http"
            except Exception as exc:
                logger.warning("HTTP unsubscribe failed (%s), trying mailto: %s", http_url, exc)

        if mailto_addr:
            try:
                self._unsubscribe_mailto(mailto_addr, email.get("sender_email", ""))
                return "mailto"
            except Exception as exc:
                logger.warning("mailto unsubscribe failed: %s", exc)

        logger.info("No working unsubscribe method found for %s", email.get("sender_email"))
        return "none"

    def _unsubscribe_http(self, url: str) -> None:
        """GET the unsubscribe URL. Does not retry on 4xx (one-time links)."""
        logger.debug("HTTP unsubscribe: GET %s", url)
        response = http_requests.get(url, timeout=10, allow_redirects=True)
        logger.info(
            "HTTP unsubscribe response: %s %s", response.status_code, url
        )
        # Do not raise on 4xx — unsubscribe links are often one-use and expire

    def _unsubscribe_mailto(self, mailto_address: str, original_sender: str) -> None:
        """Send an unsubscribe email to the mailto address."""
        logger.debug("mailto unsubscribe: sending to %s", mailto_address)
        self._client.send_email(
            to=mailto_address,
            subject="Unsubscribe",
            body="Please unsubscribe me from this mailing list.",
        )

    # -------------------------------------------------------------------------
    # Safety helpers
    # -------------------------------------------------------------------------

    def _is_whitelisted(self, email: dict) -> bool:
        """
        Returns True if the sender is on the whitelist.
        Matches exact email address OR @domain.com domain prefix.
        """
        sender_email = (email.get("sender_email") or "").lower()
        sender_domain = (email.get("sender_domain") or "").lower()

        for entry in self._whitelist:
            if entry.startswith("@"):
                # Domain match
                if sender_domain == entry[1:]:
                    return True
            else:
                # Exact email match
                if sender_email == entry:
                    return True

        return False

    def _check_bulk_limit(self) -> bool:
        """Returns True if the bulk deletion limit has been reached."""
        return self._deletion_count >= BULK_LIMIT

    # -------------------------------------------------------------------------
    # Whitelist loading
    # -------------------------------------------------------------------------

    def _load_whitelist(self, whitelist_path: str) -> set[str]:
        """
        Load the whitelist from a plain text file.
        Lines starting with '#' are comments. Empty lines are ignored.
        Entries are normalised to lowercase.
        """
        whitelist: set[str] = set()
        path = Path(whitelist_path)
        if not path.exists():
            logger.warning("Whitelist file not found: %s", whitelist_path)
            return whitelist

        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    whitelist.add(line)

        logger.debug("Loaded %d whitelist entries from %s", len(whitelist), whitelist_path)
        return whitelist

    def process_resolved_reviews(self) -> None:
        """
        Called each poll cycle. Executes actions for reviews the user has resolved.
        After executing, marks the review as actioned in the DB.
        """
        actionable = self._db.get_actionable_reviews()
        for review in actionable:
            message_id = review["message_id"]
            decision = self._db.get_sender_decision(review["sender"])

            if decision == "keep":
                logger.info("Review decision: keep %s", review["sender"])
                self._db.mark_review_actioned(message_id)

            elif decision in ("unsubscribe", "trash_only"):
                if bot_state.get_dry_run():
                    logger.info(
                        "[DRY RUN] Would execute review decision '%s' for %s",
                        decision, review["sender"],
                    )
                    self._db.mark_review_actioned(message_id)
                    continue

                if not self._check_bulk_limit():
                    try:
                        if decision == "unsubscribe":
                            # We don't have the full email dict here, just the message_id
                            # Best effort: try to trash only (unsubscribe already missed window)
                            pass
                        self._client.trash_message(message_id)
                        self._deletion_count += 1
                        logger.info(
                            "Executed review decision '%s' for %s", decision, review["sender"]
                        )
                    except GmailAPIError as exc:
                        logger.error("Failed to trash %s: %s", message_id, exc)
                        continue

                self._db.mark_review_actioned(message_id)

            else:
                # Unknown decision — mark as actioned to avoid infinite loop
                logger.warning("Unknown review decision '%s' for %s", decision, review["sender"])
                self._db.mark_review_actioned(message_id)
