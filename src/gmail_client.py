"""
gmail_client.py — Gmail API wrapper for the Gmail Automation Bot.

All Gmail API interactions go through this class.
Defines the canonical email dict consumed by all other modules.
"""

import base64
import email as email_lib
import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class GmailAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GmailClient:
    def __init__(self, credentials) -> None:
        self._service = build("gmail", "v1", credentials=credentials)
        self._user = "me"

    # -------------------------------------------------------------------------
    # History / polling
    # -------------------------------------------------------------------------

    def get_initial_history_id(self) -> str:
        """
        Called only on first run (no stored historyId).
        Returns the current historyId as a baseline — does NOT return any message IDs.
        The first poll cycle using this ID will process no emails (correct behaviour
        — prevents bulk-processing the entire inbox on startup).
        """
        try:
            result = self._service.users().messages().list(
                userId=self._user,
                maxResults=1,
                labelIds=["INBOX"],
            ).execute()

            messages = result.get("messages", [])
            if messages:
                msg = self._service.users().messages().get(
                    userId=self._user,
                    id=messages[0]["id"],
                    format="minimal",
                ).execute()
                history_id = str(msg.get("historyId", "1"))
            else:
                profile = self._service.users().getProfile(userId=self._user).execute()
                history_id = str(profile.get("historyId", "1"))

            logger.info("Initial historyId set to %s", history_id)
            return history_id

        except HttpError as exc:
            raise GmailAPIError(str(exc), exc.resp.status) from exc

    def get_history(self, start_history_id: str) -> tuple[list[str], str]:
        """
        Returns (new_message_ids, latest_history_id).
        Uses history.list with historyTypes=['messageAdded'] to find new messages.
        Returns ([], start_history_id) if nothing new.
        """
        try:
            result = self._service.users().history().list(
                userId=self._user,
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                labelId="INBOX",
            ).execute()

            new_ids: list[str] = []
            latest_id = str(result.get("historyId", start_history_id))

            for record in result.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg_id = added["message"]["id"]
                    if msg_id not in new_ids:
                        new_ids.append(msg_id)

            logger.debug("History poll: %d new message(s), historyId now %s",
                         len(new_ids), latest_id)
            return new_ids, latest_id

        except HttpError as exc:
            raise GmailAPIError(str(exc), exc.resp.status) from exc

    # -------------------------------------------------------------------------
    # Message operations
    # -------------------------------------------------------------------------

    def get_message(self, message_id: str) -> dict:
        """
        Fetch a message and return the canonical email dict:
        {
          'id', 'thread_id', 'sender', 'sender_email', 'sender_domain',
          'subject', 'received_at', 'body_text', 'has_pdf',
          'list_unsubscribe', 'raw_headers', 'labels'
        }
        """
        try:
            msg = self._service.users().messages().get(
                userId=self._user,
                id=message_id,
                format="full",
            ).execute()
        except HttpError as exc:
            raise GmailAPIError(str(exc), exc.resp.status) from exc

        payload = msg.get("payload", {})
        headers = self._extract_headers(payload)

        from_header = headers.get("From", "")
        display_name, sender_email, sender_domain = self._parse_sender(from_header)

        unsubscribe_raw = headers.get("List-Unsubscribe") or headers.get("list-unsubscribe")

        received_at = self._parse_received_at(msg.get("internalDate"))

        return {
            "id": message_id,
            "thread_id": msg.get("threadId", ""),
            "sender": display_name,
            "sender_email": sender_email,
            "sender_domain": sender_domain,
            "subject": headers.get("Subject", "(no subject)"),
            "received_at": received_at,
            "body_text": self._decode_body(payload),
            "has_pdf": self._has_pdf_attachment(payload),
            "list_unsubscribe": unsubscribe_raw,
            "raw_headers": headers,
            "labels": msg.get("labelIds", []),
        }

    def trash_message(self, message_id: str) -> None:
        try:
            self._service.users().messages().trash(
                userId=self._user,
                id=message_id,
            ).execute()
            logger.info("Trashed message %s", message_id)
        except HttpError as exc:
            raise GmailAPIError(str(exc), exc.resp.status) from exc

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> None:
        """Used for mailto: unsubscribe requests."""
        import base64
        from email.mime.text import MIMEText

        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        if in_reply_to:
            mime["In-Reply-To"] = in_reply_to

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        try:
            self._service.users().messages().send(
                userId=self._user,
                body={"raw": raw},
            ).execute()
            logger.info("Sent unsubscribe email to %s", to)
        except HttpError as exc:
            raise GmailAPIError(str(exc), exc.resp.status) from exc

    def list_messages_by_label(self, label_name: str) -> list[str]:
        """Used in tests to scope operations to a specific label."""
        try:
            labels = self._service.users().labels().list(userId=self._user).execute()
            label_id = None
            for lbl in labels.get("labels", []):
                if lbl["name"].lower() == label_name.lower():
                    label_id = lbl["id"]
                    break
            if not label_id:
                return []

            result = self._service.users().messages().list(
                userId=self._user,
                labelIds=[label_id],
            ).execute()
            return [m["id"] for m in result.get("messages", [])]
        except HttpError as exc:
            raise GmailAPIError(str(exc), exc.resp.status) from exc

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _extract_headers(self, payload: dict) -> dict[str, str]:
        """Flatten the headers list into a case-preserved dict."""
        result: dict[str, str] = {}
        for h in payload.get("headers", []):
            result[h["name"]] = h["value"]
        return result

    def _parse_sender(self, from_header: str) -> tuple[str, str, str]:
        """
        Returns (display_name, email_address, domain).
        Handles 'Name <email@domain.com>' and bare 'email@domain.com'.
        """
        from_header = from_header.strip()
        match = re.match(r'^"?([^"<]+?)"?\s*<([^>]+)>', from_header)
        if match:
            display_name = match.group(1).strip()
            email_address = match.group(2).strip().lower()
        else:
            email_address = from_header.lower()
            display_name = email_address

        domain = email_address.split("@")[-1] if "@" in email_address else ""
        return display_name, email_address, domain

    def _decode_body(self, payload: dict) -> str:
        """
        Walk MIME parts and extract the best available text.
        Priority: text/plain > HTML-stripped text/html.
        """
        plain_text = ""
        html_text = ""

        def walk(part: dict) -> None:
            nonlocal plain_text, html_text
            mime_type = part.get("mimeType", "")
            body = part.get("body", {})
            data = body.get("data", "")

            if mime_type == "text/plain" and data and not plain_text:
                try:
                    plain_text = base64.urlsafe_b64decode(
                        data + "=="
                    ).decode("utf-8", errors="replace")
                except Exception:
                    pass

            elif mime_type == "text/html" and data and not html_text:
                try:
                    raw = base64.urlsafe_b64decode(
                        data + "=="
                    ).decode("utf-8", errors="replace")
                    soup = BeautifulSoup(raw, "lxml")
                    html_text = soup.get_text(separator=" ", strip=True)
                except Exception:
                    pass

            for sub in part.get("parts", []):
                walk(sub)

        walk(payload)
        return plain_text or html_text

    def _has_pdf_attachment(self, payload: dict) -> bool:
        """Return True if any MIME part looks like a PDF attachment."""
        pdf_mimes = {"application/pdf", "application/octet-stream"}

        def walk(part: dict) -> bool:
            mime_type = part.get("mimeType", "").lower()
            filename = (part.get("filename") or "").lower()
            if mime_type in pdf_mimes or filename.endswith(".pdf"):
                body = part.get("body", {})
                if body.get("attachmentId") or body.get("size", 0) > 0:
                    return True
            for sub in part.get("parts", []):
                if walk(sub):
                    return True
            return False

        return walk(payload)

    def _parse_list_unsubscribe(self, header_value: str | None) -> dict:
        """
        Parse List-Unsubscribe header.
        Returns {'mailto': str|None, 'http': str|None}.
        Header may contain multiple comma-separated values in angle brackets.
        """
        result: dict[str, str | None] = {"mailto": None, "http": None}
        if not header_value:
            return result

        parts = re.findall(r"<([^>]+)>", header_value)
        for part in parts:
            part = part.strip()
            if part.lower().startswith("mailto:") and not result["mailto"]:
                result["mailto"] = part[len("mailto:"):]
            elif part.lower().startswith("http") and not result["http"]:
                result["http"] = part

        return result

    def _parse_received_at(self, internal_date: str | None) -> str:
        """Convert Gmail internalDate (epoch ms) to UTC ISO-8601 string."""
        if not internal_date:
            return datetime.now(timezone.utc).isoformat()
        try:
            ts = int(internal_date) / 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, TypeError):
            return datetime.now(timezone.utc).isoformat()
