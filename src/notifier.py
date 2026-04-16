"""
notifier.py — Windows desktop toast notification dispatch.

Uses plyer (which wraps win10toast on Windows) for native toast pop-ups.
All notification dispatch for the bot goes through this module only.
Handles quiet hours suppression and message formatting.
"""

import logging
import webbrowser
from datetime import datetime

logger = logging.getLogger(__name__)

GMAIL_URL = "https://mail.google.com/mail/u/0/#inbox/{message_id}"
APP_NAME = "Gmail Bot"
TOAST_DURATION = 6  # seconds before toast fades


class NotifierError(Exception):
    """Raised when a notification cannot be delivered."""


class Notifier:
    def __init__(
        self,
        quiet_hours_start: int = 22,
        quiet_hours_end: int = 7,
    ) -> None:
        self._quiet_start = quiet_hours_start
        self._quiet_end = quiet_hours_end
        self._toast = self._load_toast()

    def _load_toast(self):
        """
        Attempt to load win10toast for click callback support.
        Falls back to plyer if win10toast is unavailable.
        Returns None if neither is available (graceful degradation).
        """
        try:
            from win10toast import ToastNotifier
            return ToastNotifier()
        except ImportError:
            logger.debug("win10toast not available; will use plyer")
            return None

    # -------------------------------------------------------------------------
    # Core send
    # -------------------------------------------------------------------------

    def send(
        self,
        title: str,
        body: str,
        click_url: str | None = None,
        force: bool = False,
    ) -> bool:
        """
        Send a Windows toast notification.

        Returns True if sent, False if suppressed (quiet hours).
        Raises NotifierError if delivery fails.
        force=True bypasses quiet hours (used for critical errors).
        """
        if not force and self.is_quiet_hours():
            logger.debug("Suppressed notification (quiet hours): %s", title)
            return False

        try:
            self._dispatch(title, body, click_url)
            logger.info("Notification sent: %s", title)
            return True
        except Exception as exc:
            raise NotifierError(f"Failed to send notification: {exc}") from exc

    def _dispatch(self, title: str, body: str, click_url: str | None) -> None:
        """Internal: send via win10toast (with click support) or plyer."""
        if self._toast is not None:
            # win10toast supports callback_on_click
            callback = None
            if click_url:
                url = click_url
                callback = lambda: webbrowser.open(url)

            self._toast.show_toast(
                title=title,
                msg=body,
                app_id=APP_NAME,
                duration=TOAST_DURATION,
                threaded=True,
                callback_on_click=callback,
            )
        else:
            try:
                from plyer import notification
                notification.notify(
                    title=title,
                    message=body,
                    app_name=APP_NAME,
                    timeout=TOAST_DURATION,
                )
            except Exception as exc:
                raise NotifierError(f"plyer notification failed: {exc}") from exc

    # -------------------------------------------------------------------------
    # Quiet hours
    # -------------------------------------------------------------------------

    def is_quiet_hours(self) -> bool:
        """
        Returns True if current local time is within quiet hours.
        Handles overnight ranges (e.g., 22:00 to 07:00).
        """
        current_hour = datetime.now().hour
        start = self._quiet_start
        end = self._quiet_end

        if start > end:
            # Overnight range: e.g., 22 to 7
            return current_hour >= start or current_hour < end
        else:
            # Same-day range: e.g., 13 to 17
            return start <= current_hour < end

    # -------------------------------------------------------------------------
    # Message formatters
    # -------------------------------------------------------------------------

    def format_important(
        self,
        classification_detail: str,
        sender_name: str,
        message_id: str,
        subject: str = "",
        summary: str | None = None,
    ) -> tuple[str, str]:
        """
        Returns (title, body) for an important email notification.
        group_a: title=sender_name, body=5-word summary from body text
        group_b: title="Ticket: {sender}", body="PDF attached"
        group_c: title="Job alert: {sender}", body=subject (truncated)
        """
        url = GMAIL_URL.format(message_id=message_id)

        if classification_detail == "group_b":
            return (f"Ticket: {sender_name}", f"PDF attached — {subject[:50]}")

        if classification_detail == "group_c":
            return (f"Job alert: {sender_name}", subject[:60] or "New job response")

        # group_a — personal
        body = summary or _five_word_summary(subject)
        return (sender_name, body)

    def format_review_needed(self, count: int) -> tuple[str, str]:
        return (
            "Review needed",
            f"{count} newsletter{'s' if count != 1 else ''} need your review",
        )

    def format_auth_failure(self) -> tuple[str, str]:
        return (
            f"{APP_NAME}: Auth Error",
            "Re-authentication required. Run setup.",
        )

    def format_overnight_digest(self, count: int) -> tuple[str, str]:
        return (
            f"{APP_NAME}: Overnight digest",
            f"{count} important email{'s' if count != 1 else ''} while you were away",
        )

    def format_critical_error(self, error_type: str) -> tuple[str, str]:
        return (
            f"{APP_NAME}: CRITICAL",
            f"Error: {error_type}. Check logs/",
        )

    def format_bulk_paused(self, count: int) -> tuple[str, str]:
        return (
            f"{APP_NAME}: Bulk pause",
            f"Paused — {count} newsletters in this cycle. Check review page.",
        )


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _five_word_summary(text: str) -> str:
    """
    Extract up to 5 words from the start of the text.
    Used for Group A personal email summaries (no LLM required).
    """
    if not text:
        return "New personal email"
    words = text.strip().split()
    return " ".join(words[:5])
