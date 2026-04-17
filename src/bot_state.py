"""
bot_state.py — Thread-safe in-process runtime state shared across all modules.

This is the single source of truth for whether the bot is currently in Safe Mode
(dry-run). All modules read from here at runtime rather than from .env.

Write path:
  - main.py       → set_dry_run() at startup (hydrates from .env)
  - review_server → set_dry_run() when user toggles Safe Mode in dashboard

Read path:
  - newsletter_manager → get_dry_run() before every action
"""

import threading

_lock = threading.Lock()
_dry_run: bool = False


def get_dry_run() -> bool:
    """Return the current live dry-run flag. Thread-safe."""
    with _lock:
        return _dry_run


def set_dry_run(value: bool) -> None:
    """Update the live dry-run flag. Takes effect immediately. Thread-safe."""
    with _lock:
        global _dry_run
        _dry_run = value
