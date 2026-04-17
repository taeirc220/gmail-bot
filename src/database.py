"""
database.py — SQLite interface for the Gmail Automation Bot.

All database reads and writes go through this module.
No other module accesses the DB file directly.
"""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_CLASSIFICATIONS = {"important", "newsletter", "unsure", "ignored"}
VALID_ACTIONS = {
    "notified", "trashed", "queued", "none",
    "dry_run_would_trash", "skipped_whitelist", "paused_bulk_limit",
    "unsubscribed_and_trashed", "restored_from_trash",
}
VALID_RULE_TYPES = {"force_important", "force_ignore", "force_newsletter"}
VALID_DECISIONS = {"keep", "unsubscribe", "trash_only"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.debug("Database initialised at %s", db_path)

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS emails_processed (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id           TEXT UNIQUE NOT NULL,
                sender               TEXT,
                subject              TEXT,
                received_at          TEXT,
                classification       TEXT,
                classification_detail TEXT,
                action_taken         TEXT,
                processed_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS newsletter_decisions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sender      TEXT,
                domain      TEXT,
                decision    TEXT NOT NULL,
                decided_by  TEXT NOT NULL,
                decided_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS pending_review (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id   TEXT UNIQUE NOT NULL,
                sender       TEXT,
                subject      TEXT,
                received_at  TEXT,
                flag_reason  TEXT,
                added_at     TEXT,
                resolved     INTEGER DEFAULT 0,
                action_taken INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS errors_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                error_type  TEXT,
                message     TEXT,
                resolved    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS sender_rules (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_email TEXT NOT NULL UNIQUE,
                rule_type    TEXT NOT NULL,
                set_by       TEXT NOT NULL DEFAULT 'user',
                set_at       TEXT
            );
        """)
        self._conn.commit()

    # -------------------------------------------------------------------------
    # emails_processed
    # -------------------------------------------------------------------------

    def record_processed(
        self,
        message_id: str,
        sender: str,
        subject: str,
        received_at: str,
        classification: str,
        classification_detail: str | None,
        action_taken: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO emails_processed
                (message_id, sender, subject, received_at,
                 classification, classification_detail, action_taken, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id, sender, subject, received_at,
                classification, classification_detail, action_taken, _utcnow(),
            ),
        )
        self._conn.commit()

    def is_already_processed(self, message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT id FROM emails_processed WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    # -------------------------------------------------------------------------
    # newsletter_decisions
    # -------------------------------------------------------------------------

    def get_sender_decision(self, sender_email: str) -> str | None:
        """Return the most recent decision for this sender, or None."""
        row = self._conn.execute(
            """
            SELECT decision FROM newsletter_decisions
            WHERE sender = ?
            ORDER BY id DESC LIMIT 1
            """,
            (sender_email,),
        ).fetchone()
        return row["decision"] if row else None

    def record_decision(
        self,
        sender: str,
        domain: str,
        decision: str,
        decided_by: str,
    ) -> None:
        assert decision in VALID_DECISIONS, f"Invalid decision: {decision!r}"
        self._conn.execute(
            """
            INSERT INTO newsletter_decisions (sender, domain, decision, decided_by, decided_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sender, domain, decision, decided_by, _utcnow()),
        )
        self._conn.commit()

    # -------------------------------------------------------------------------
    # pending_review
    # -------------------------------------------------------------------------

    def add_pending_review(
        self,
        message_id: str,
        sender: str,
        subject: str,
        received_at: str,
        flag_reason: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO pending_review
                (message_id, sender, subject, received_at, flag_reason, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, sender, subject, received_at, flag_reason, _utcnow()),
        )
        self._conn.commit()

    def get_pending_reviews(self, unresolved_only: bool = True) -> list[dict]:
        query = "SELECT * FROM pending_review"
        if unresolved_only:
            query += " WHERE resolved = 0"
        query += " ORDER BY added_at ASC"
        rows = self._conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    def get_actionable_reviews(self) -> list[dict]:
        """Reviews the user has decided on that the bot has not yet acted on."""
        rows = self._conn.execute(
            "SELECT * FROM pending_review WHERE resolved = 1 AND action_taken = 0"
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_pending(self, message_id: str, decision: str) -> None:
        """Mark a pending review as resolved and record the user's decision."""
        assert decision in VALID_DECISIONS, f"Invalid decision: {decision!r}"
        row = self._conn.execute(
            "SELECT sender, subject FROM pending_review WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            logger.warning("resolve_pending: message_id %s not found", message_id)
            return

        sender = row["sender"] or ""
        domain = sender.split("@")[-1] if "@" in sender else ""

        self._conn.execute(
            "UPDATE pending_review SET resolved = 1 WHERE message_id = ?",
            (message_id,),
        )
        self._conn.commit()
        self.record_decision(sender, domain, decision, "user")

    def mark_review_actioned(self, message_id: str) -> None:
        """Mark that the bot has executed the action for a resolved review."""
        self._conn.execute(
            "UPDATE pending_review SET action_taken = 1 WHERE message_id = ?",
            (message_id,),
        )
        self._conn.commit()

    def pending_review_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM pending_review WHERE resolved = 0"
        ).fetchone()
        return row["cnt"]

    # -------------------------------------------------------------------------
    # errors_log
    # -------------------------------------------------------------------------

    def log_error(self, error_type: str, message: str) -> None:
        self._conn.execute(
            "INSERT INTO errors_log (timestamp, error_type, message) VALUES (?, ?, ?)",
            (_utcnow(), error_type, message),
        )
        self._conn.commit()

    # -------------------------------------------------------------------------
    # config (key/value store for historyId etc.)
    # -------------------------------------------------------------------------

    def get_config(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_config(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_last_history_id(self) -> str | None:
        return self.get_config("last_history_id")

    def set_last_history_id(self, history_id: str) -> None:
        self.set_config("last_history_id", history_id)

    # -------------------------------------------------------------------------
    # Dashboard queries
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Aggregate counts for dashboard stat cards."""
        total = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM emails_processed"
        ).fetchone()["cnt"]

        important = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM emails_processed WHERE classification = 'important'"
        ).fetchone()["cnt"]

        newsletters = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM emails_processed WHERE classification = 'newsletter'"
        ).fetchone()["cnt"]

        ignored = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM emails_processed WHERE classification = 'ignored'"
        ).fetchone()["cnt"]

        today = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM emails_processed "
            "WHERE DATE(processed_at) = DATE('now')"
        ).fetchone()["cnt"]

        pending = self.pending_review_count()

        errors = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM errors_log WHERE resolved = 0"
        ).fetchone()["cnt"]

        return {
            "total_processed": total,
            "total_important": important,
            "total_newsletters": newsletters,
            "total_ignored": ignored,
            "today_processed": today,
            "pending_reviews": pending,
            "unresolved_errors": errors,
        }

    def get_recent_emails(self, limit: int = 50) -> list[dict]:
        """Most recently processed emails, newest first."""
        rows = self._conn.execute(
            """
            SELECT message_id, sender, subject, classification,
                   classification_detail, action_taken, processed_at
            FROM emails_processed
            ORDER BY processed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_emails_page(self, page: int = 1, per_page: int = 25,
                        classification: str | None = None) -> tuple[list[dict], int]:
        """Paginated email history with optional classification filter.
        Returns (rows, total_count).
        """
        base = "FROM emails_processed"
        params: list = []
        if classification and classification != "all":
            base += " WHERE classification = ?"
            params.append(classification)

        total = self._conn.execute(
            f"SELECT COUNT(*) AS cnt {base}", params
        ).fetchone()["cnt"]

        offset = (page - 1) * per_page
        rows = self._conn.execute(
            f"SELECT message_id, sender, subject, classification, "
            f"classification_detail, action_taken, processed_at {base} "
            f"ORDER BY processed_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    def get_activity_by_day(self, days: int = 7) -> list[dict]:
        """Per-day counts by classification for the last N days (for Chart.js)."""
        rows = self._conn.execute(
            """
            SELECT
                DATE(processed_at) AS date,
                SUM(CASE WHEN classification = 'important'  THEN 1 ELSE 0 END) AS important,
                SUM(CASE WHEN classification = 'newsletter' THEN 1 ELSE 0 END) AS newsletter,
                SUM(CASE WHEN classification = 'ignored'    THEN 1 ELSE 0 END) AS ignored,
                SUM(CASE WHEN classification = 'unsure'     THEN 1 ELSE 0 END) AS unsure
            FROM emails_processed
            WHERE DATE(processed_at) >= DATE('now', ? || ' days')
            GROUP BY DATE(processed_at)
            ORDER BY date ASC
            """,
            (f"-{days}",),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_decisions(self) -> list[dict]:
        """All newsletter sender decisions, newest first."""
        rows = self._conn.execute(
            """
            SELECT sender, domain, decision, decided_by, decided_at
            FROM newsletter_decisions
            ORDER BY decided_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_errors(self, limit: int = 20) -> list[dict]:
        """Most recent errors from errors_log."""
        rows = self._conn.execute(
            "SELECT id, timestamp, error_type, message, resolved "
            "FROM errors_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_error_resolved(self, error_id: int) -> None:
        self._conn.execute(
            "UPDATE errors_log SET resolved = 1 WHERE id = ?", (error_id,)
        )
        self._conn.commit()

    def update_action_taken(self, message_id: str, new_action: str) -> None:
        """Update action_taken for an already-processed email (e.g. after untrash)."""
        self._conn.execute(
            "UPDATE emails_processed SET action_taken = ? WHERE message_id = ?",
            (new_action, message_id),
        )
        self._conn.commit()
        logger.debug("Updated action_taken for %s → %s", message_id, new_action)

    # -------------------------------------------------------------------------
    # sender_rules
    # -------------------------------------------------------------------------

    def get_sender_rule(self, sender_email: str) -> dict | None:
        """Return the rule for this sender, or None if no rule exists."""
        row = self._conn.execute(
            "SELECT * FROM sender_rules WHERE sender_email = ?",
            (sender_email.lower(),),
        ).fetchone()
        return dict(row) if row else None

    def set_sender_rule(self, sender_email: str, rule_type: str,
                        set_by: str = "user") -> None:
        """Create or replace a sender rule. rule_type must be in VALID_RULE_TYPES."""
        assert rule_type in VALID_RULE_TYPES, f"Invalid rule_type: {rule_type!r}"
        self._conn.execute(
            """
            INSERT INTO sender_rules (sender_email, rule_type, set_by, set_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sender_email) DO UPDATE SET
                rule_type = excluded.rule_type,
                set_by    = excluded.set_by,
                set_at    = excluded.set_at
            """,
            (sender_email.lower(), rule_type, set_by, _utcnow()),
        )
        self._conn.commit()
        logger.info("Sender rule set: %s → %s", sender_email, rule_type)

    def delete_sender_rule(self, sender_email: str) -> None:
        """Remove the rule for this sender."""
        self._conn.execute(
            "DELETE FROM sender_rules WHERE sender_email = ?",
            (sender_email.lower(),),
        )
        self._conn.commit()
        logger.info("Sender rule deleted: %s", sender_email)

    def get_all_sender_rules(self) -> list[dict]:
        """All sender rules, newest first."""
        rows = self._conn.execute(
            "SELECT sender_email, rule_type, set_by, set_at "
            "FROM sender_rules ORDER BY set_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
