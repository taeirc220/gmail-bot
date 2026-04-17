"""
main.py — Entry point and polling loop for the Gmail Automation Bot.

Orchestrates all modules. Runs a poll cycle every 1 minute via the
`schedule` library. Handles quiet hours, overnight digest, and error recovery.

Run directly: python src/main.py
Run silently (no console): pythonw src/main.py
"""

import logging
import logging.handlers
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import schedule
import socket
import yaml
from dotenv import load_dotenv

# Ensure src/ is importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.auth import get_credentials, AuthError
from src.classifier import classify
from src.database import Database
from src.gmail_client import GmailClient, GmailAPIError
from src.newsletter_manager import NewsletterManager
from src.notifier import Notifier
import src.review_server as review_server
from src.tray_icon import TrayIcon

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


# -------------------------------------------------------------------------
# Configuration loading
# -------------------------------------------------------------------------

def load_config() -> dict:
    """Load .env and importance_rules.yaml. Validate required keys."""
    env_file = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=str(env_file), override=False)

    required = [
        "GMAIL_CREDENTIALS_PATH", "GMAIL_TOKEN_PATH",
        "REVIEW_SERVER_SECRET", "DB_PATH",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise ConfigError(f"Missing required .env keys: {', '.join(missing)}")

    rules_path = Path(__file__).parent.parent / "config" / "importance_rules.yaml"
    if not rules_path.exists():
        raise ConfigError(f"importance_rules.yaml not found at {rules_path}")

    with open(rules_path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)

    return {
        "credentials_path": os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json"),
        "token_path": os.getenv("GMAIL_TOKEN_PATH", "token.json"),
        "review_secret": os.getenv("REVIEW_SERVER_SECRET"),
        "review_port": int(os.getenv("REVIEW_SERVER_PORT", "8080")),
        "db_path": os.getenv("DB_PATH", "data/gmail_bot.db"),
        "dry_run": os.getenv("DRY_RUN", "false").lower() == "true",
        "test_mode": os.getenv("TEST_MODE", "false").lower() == "true",
        "quiet_hours_start": int(os.getenv("QUIET_HOURS_START", "22")),
        "quiet_hours_end": int(os.getenv("QUIET_HOURS_END", "7")),
        "whitelist_path": str(
            Path(__file__).parent.parent / "config" / "newsletter_whitelist.txt"
        ),
        "rules": rules,
    }


# -------------------------------------------------------------------------
# Logging setup
# -------------------------------------------------------------------------

def setup_logging(log_dir: str = "logs") -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"bot_{datetime.now().strftime('%Y-%m-%d')}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Rotating file handler: 5MB, keep 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    # Also log to stdout (useful for first-run diagnostics)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    stdout_handler.setLevel(logging.INFO)
    root.addHandler(stdout_handler)


# -------------------------------------------------------------------------
# Component initialisation
# -------------------------------------------------------------------------

def initialize_components(config: dict):
    """Instantiate all components. Raises on auth failure."""
    db = Database(config["db_path"])

    try:
        creds = get_credentials(config["credentials_path"], config["token_path"])
    except AuthError as exc:
        logger.critical("Authentication failed: %s", exc)
        # Attempt to send a notification even without a valid client
        notifier = Notifier(
            quiet_hours_start=config["quiet_hours_start"],
            quiet_hours_end=config["quiet_hours_end"],
        )
        title, body = notifier.format_auth_failure()
        try:
            notifier.send(title, body, force=True)
        except Exception:
            pass
        raise

    gmail_client = GmailClient(creds)

    notifier = Notifier(
        quiet_hours_start=config["quiet_hours_start"],
        quiet_hours_end=config["quiet_hours_end"],
    )

    newsletter_manager = NewsletterManager(
        gmail_client=gmail_client,
        db=db,
        notifier=notifier,
        whitelist_path=config["whitelist_path"],
        dry_run=config["dry_run"],
    )

    return db, gmail_client, notifier, newsletter_manager


# -------------------------------------------------------------------------
# Poll cycle
# -------------------------------------------------------------------------

def poll_cycle(
    gmail_client: GmailClient,
    db: Database,
    notifier: Notifier,
    newsletter_manager: NewsletterManager,
    rules: dict,
    overnight_buffer: list,
    pause_event: threading.Event | None = None,
) -> None:
    """
    Single 1-minute poll cycle.
    1. Reset newsletter bulk counter
    2. Get new messages via history API
    3. Classify and dispatch each message
    4. Process any resolved newsletter reviews
    """
    if pause_event is not None and pause_event.is_set():
        logger.debug("Polling paused — skipping cycle")
        return

    newsletter_manager.reset_cycle_counter()
    quiet = notifier.is_quiet_hours()

    history_id = db.get_last_history_id()

    if history_id is None:
        logger.info("First run — setting history baseline, no emails processed this cycle")
        try:
            initial_id = gmail_client.get_initial_history_id()
            db.set_last_history_id(initial_id)
        except GmailAPIError as exc:
            logger.error("Failed to get initial history ID: %s", exc)
        return

    try:
        new_ids, new_history_id = gmail_client.get_history(history_id)
    except GmailAPIError as exc:
        logger.error("get_history failed: %s", exc)
        raise

    # Store new historyId BEFORE processing — prevents reprocessing if we crash mid-cycle
    if new_history_id != history_id:
        db.set_last_history_id(new_history_id)

    for message_id in new_ids:
        if db.is_already_processed(message_id):
            logger.debug("Skipping already-processed message: %s", message_id)
            continue

        try:
            email = gmail_client.get_message(message_id)
        except GmailAPIError as exc:
            logger.warning("Failed to fetch message %s: %s", message_id, exc)
            continue

        result = classify(email, rules, db)
        _dispatch(email, result, db, notifier, newsletter_manager, quiet, overnight_buffer)

    # Process any newsletter reviews the user resolved via the review page
    try:
        newsletter_manager.process_resolved_reviews()
    except Exception as exc:
        logger.warning("Error processing resolved reviews: %s", exc)


def _dispatch(
    email: dict,
    result: tuple,
    db: Database,
    notifier: Notifier,
    newsletter_manager: NewsletterManager,
    quiet: bool,
    overnight_buffer: list,
) -> None:
    """Route a classified email to its appropriate action."""
    classification, detail = result

    if classification == "important":
        if quiet:
            overnight_buffer.append({
                "sender": email.get("sender", ""),
                "subject": email.get("subject", ""),
            })
            logger.info("Buffered (quiet hours): %s | %s",
                        email.get("sender"), email.get("subject"))
        else:
            from src.notifier import _five_word_summary
            summary = _five_word_summary(email.get("body_text", ""))
            title, body = notifier.format_important(
                classification_detail=detail,
                sender_name=email.get("sender", "Unknown"),
                message_id=email.get("id", ""),
                subject=email.get("subject", ""),
                summary=summary,
            )
            try:
                notifier.send(title, body,
                              click_url=f"https://mail.google.com/mail/u/0/#inbox/{email['id']}")
            except Exception as exc:
                logger.warning("Failed to send notification: %s", exc)

        db.record_processed(
            email["id"], email.get("sender", ""), email.get("subject", ""),
            email.get("received_at", ""), "important", detail, "notified",
        )

    elif classification == "newsletter":
        newsletter_manager.handle(email, detail)  # detail is confidence: 'high'|'low'

    elif classification == "unsure":
        db.add_pending_review(
            message_id=email["id"],
            sender=email.get("sender_email", ""),
            subject=email.get("subject", ""),
            received_at=email.get("received_at", ""),
            flag_reason=detail or "No specific reason",
        )
        db.record_processed(
            email["id"], email.get("sender", ""), email.get("subject", ""),
            email.get("received_at", ""), "unsure", detail, "queued",
        )
        logger.info("Queued as unsure: %s | %s", email.get("sender"), email.get("subject"))

    else:  # 'ignored'
        db.record_processed(
            email["id"], email.get("sender", ""), email.get("subject", ""),
            email.get("received_at", ""), "ignored", None, "none",
        )


# -------------------------------------------------------------------------
# Overnight digest
# -------------------------------------------------------------------------

def send_overnight_digest(notifier: Notifier, overnight_buffer: list) -> None:
    """
    Fires at 07:00. Sends a single notification summarising important emails
    that arrived during quiet hours. Clears the buffer.
    """
    if not overnight_buffer:
        return

    count = len(overnight_buffer)
    title, body = notifier.format_overnight_digest(count)
    try:
        notifier.send(title, body, force=True)
        logger.info("Overnight digest sent: %d emails", count)
    except Exception as exc:
        logger.warning("Failed to send overnight digest: %s", exc)
    finally:
        overnight_buffer.clear()


# -------------------------------------------------------------------------
# Retry wrapper
# -------------------------------------------------------------------------

def _with_retry(func, args: tuple, kwargs: dict, db: Database, notifier: Notifier) -> None:
    """
    Call func(*args, **kwargs) with exponential backoff on GmailAPIError.
    After 3 failed retries: log CRITICAL, send forced notification, pause 10 minutes.
    """
    delays = [5, 15, 45]
    for attempt, delay in enumerate(delays, start=1):
        try:
            func(*args, **kwargs)
            return
        except GmailAPIError as exc:
            logger.warning(
                "GmailAPIError on attempt %d/%d: %s. Retrying in %ds",
                attempt, len(delays), exc, delay,
            )
            db.log_error("GmailAPIError", str(exc))
            time.sleep(delay)
        except Exception as exc:
            logger.error("Unexpected error in poll cycle: %s", exc, exc_info=True)
            db.log_error("UnexpectedError", str(exc))
            return

    logger.critical("All retries exhausted. Pausing polling for 10 minutes.")
    title, body = notifier.format_critical_error("API failure after 3 retries")
    try:
        notifier.send(title, body, force=True)
    except Exception:
        pass
    time.sleep(600)  # pause 10 minutes before next scheduled cycle


# -------------------------------------------------------------------------
# Main entry point
# -------------------------------------------------------------------------

def _find_available_port(start: int, attempts: int = 5) -> int:
    """Find the first available port starting from `start`."""
    for port in range(start, start + attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start  # fallback — let Flask report the error naturally


def main() -> None:
    setup_logging()

    # ----------------------------------------------------------------
    # Single-instance lock — prevents duplicate bot processes
    # ----------------------------------------------------------------
    try:
        import ctypes
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "GmailBotSingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            logger.info("Another Gmail Bot instance is already running. Exiting.")
            sys.exit(0)
    except Exception:
        pass  # Non-Windows or ctypes unavailable — skip the lock

    logger.info("Gmail Automation Bot starting")

    try:
        config = load_config()
    except ConfigError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    if config["dry_run"]:
        logger.warning("DRY_RUN mode is ENABLED — no destructive actions will be taken")
    if config["test_mode"]:
        logger.warning("TEST_MODE is ENABLED — operations restricted to BotTestInbox")

    try:
        db, gmail_client, notifier, newsletter_manager = initialize_components(config)
    except AuthError:
        sys.exit(1)

    # ----------------------------------------------------------------
    # Threading events for pause/resume and graceful shutdown
    # ----------------------------------------------------------------
    pause_event = threading.Event()   # set = polling paused
    stop_event  = threading.Event()   # set = bot should exit

    # ----------------------------------------------------------------
    # Dashboard server — find an available port automatically
    # ----------------------------------------------------------------
    actual_port = _find_available_port(config["review_port"])
    if actual_port != config["review_port"]:
        logger.warning(
            "Port %d was in use — dashboard running on port %d instead",
            config["review_port"], actual_port,
        )

    env_path = str(Path(__file__).parent.parent / ".env")
    review_thread = threading.Thread(
        target=review_server.start_server,
        args=(config["db_path"], config["review_secret"], actual_port),
        kwargs={"whitelist_path": config["whitelist_path"], "env_path": env_path},
        daemon=True,
        name="ReviewServer",
    )
    review_thread.start()
    logger.info("Dashboard running at http://localhost:%d/?token=***", actual_port)

    # ----------------------------------------------------------------
    # System tray icon
    # ----------------------------------------------------------------
    tray = TrayIcon(
        secret=config["review_secret"],
        port=actual_port,
        pause_event=pause_event,
        stop_event=stop_event,
    )
    tray_thread = threading.Thread(target=tray.start, daemon=True, name="TrayIcon")
    tray_thread.start()

    # ----------------------------------------------------------------
    # Startup toast — tells the user the bot is alive
    # ----------------------------------------------------------------
    try:
        notifier.send(
            "Gmail Bot is running",
            "Click the tray icon to open your dashboard.",
            force=True,
        )
    except Exception:
        pass

    overnight_buffer: list = []
    rules = config["rules"]

    def run_poll_cycle():
        _with_retry(
            poll_cycle,
            args=(gmail_client, db, notifier, newsletter_manager, rules,
                  overnight_buffer, pause_event),
            kwargs={},
            db=db,
            notifier=notifier,
        )

    # Poll every 1 minute
    schedule.every(1).minutes.do(run_poll_cycle)

    # Overnight digest fires at 07:00
    schedule.every().day.at("07:00").do(
        send_overnight_digest, notifier, overnight_buffer
    )

    logger.info("Polling started. Press Ctrl+C to stop.")

    while not stop_event.is_set():
        schedule.run_pending()
        time.sleep(30)

    logger.info("Gmail Bot shutting down cleanly.")


if __name__ == "__main__":
    main()
