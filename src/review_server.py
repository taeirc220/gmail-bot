"""
review_server.py — Local Flask dashboard for Gmail Bot.

Runs on localhost:8080 in a background daemon thread inside main.py.
Not internet-facing — local machine only.
Protected by REVIEW_SERVER_SECRET token in the URL query string.

Pages:
  GET  /                        → Dashboard (stats + activity chart)
  GET  /review                  → Newsletter review queue
  GET  /history                 → Paginated email history
  GET  /decisions               → Newsletter sender decisions
  GET  /settings                → Whitelist editor + DRY_RUN toggle + error log

API:
  GET  /api/stats               → JSON stats for Chart.js refresh
  POST /api/decision            → Single newsletter decision
  POST /api/decision/bulk       → Bulk newsletter decisions
  POST /api/whitelist/add       → Add whitelist entry
  POST /api/whitelist/remove    → Remove whitelist entry
  POST /api/dry_run/toggle      → Flip DRY_RUN in .env
  POST /api/errors/resolve      → Mark error resolved
  GET  /health                  → Health check (no token required)
"""

import hmac
import json
import logging
import os
import re
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request

from src.database import Database
from src.review_generator import (
    generate_dashboard,
    generate_decisions_page,
    generate_history_page,
    generate_review_page,
    generate_settings_page,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Set by start_server() before Flask starts
_db_path: str = ""
_secret: str = ""
_whitelist_path: str = ""
_env_path: str = ""


def _get_db() -> Database:
    return Database(_db_path)


def _check_token() -> None:
    """Abort 403 if token query param does not match the secret."""
    token = request.args.get("token", "")
    if not hmac.compare_digest(token, _secret):
        logger.warning("Rejected request with invalid token from %s", request.remote_addr)
        abort(403)


def _load_whitelist() -> list[str]:
    path = Path(_whitelist_path)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line.lower())
    return entries


def _save_whitelist(entries: list[str]) -> None:
    path = Path(_whitelist_path)
    path.write_text(
        "# Gmail Bot — newsletter sender whitelist\n"
        "# One entry per line. Exact email or @domain.com for entire domain.\n"
        + "\n".join(sorted(set(entries))) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    _check_token()
    db = _get_db()
    return generate_dashboard(db, _secret)


@app.route("/review")
def review():
    _check_token()
    db = _get_db()
    return generate_review_page(db, _secret)


@app.route("/history")
def history():
    _check_token()
    db = _get_db()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    classification = request.args.get("classification", "all")
    return generate_history_page(db, _secret, page=page, classification=classification)


@app.route("/decisions")
def decisions():
    _check_token()
    db = _get_db()
    return generate_decisions_page(db, _secret)


@app.route("/settings")
def settings():
    _check_token()
    db = _get_db()
    entries = _load_whitelist()
    dry_run = _get_dry_run_flag()
    return generate_settings_page(db, _secret, entries, dry_run)


# ---------------------------------------------------------------------------
# API — decisions
# ---------------------------------------------------------------------------

@app.route("/api/decision", methods=["POST"])
def api_decision():
    _check_token()
    message_id = request.form.get("message_id", "").strip()
    dec = request.form.get("decision", "").strip()
    if not message_id:
        abort(400, "message_id is required")
    if dec not in {"keep", "unsubscribe", "trash_only"}:
        abort(400, "invalid decision")
    db = _get_db()
    db.resolve_pending(message_id, dec)
    logger.info("Review decision: %s → %s", message_id, dec)
    return redirect(f"/review?token={_secret}")


@app.route("/api/decision/bulk", methods=["POST"])
def api_decision_bulk():
    _check_token()
    message_ids = request.form.getlist("message_ids")
    dec = request.form.get("decision", "").strip()
    if not message_ids:
        abort(400, "no message_ids provided")
    if dec not in {"keep", "unsubscribe", "trash_only"}:
        abort(400, "invalid decision")
    db = _get_db()
    for mid in message_ids:
        mid = mid.strip()
        if mid:
            db.resolve_pending(mid, dec)
    logger.info("Bulk review decision: %s applied to %d items", dec, len(message_ids))
    return redirect(f"/review?token={_secret}")


# ---------------------------------------------------------------------------
# API — whitelist
# ---------------------------------------------------------------------------

@app.route("/api/whitelist/add", methods=["POST"])
def api_whitelist_add():
    _check_token()
    entry = request.form.get("entry", "").strip().lower()
    if not entry:
        abort(400, "entry is required")
    entries = _load_whitelist()
    if entry not in entries:
        entries.append(entry)
        _save_whitelist(entries)
        logger.info("Whitelist entry added: %s", entry)
    return redirect(f"/settings?token={_secret}")


@app.route("/api/whitelist/remove", methods=["POST"])
def api_whitelist_remove():
    _check_token()
    entry = request.form.get("entry", "").strip().lower()
    entries = _load_whitelist()
    entries = [e for e in entries if e != entry]
    _save_whitelist(entries)
    logger.info("Whitelist entry removed: %s", entry)
    return redirect(f"/settings?token={_secret}")


# ---------------------------------------------------------------------------
# API — DRY_RUN toggle
# ---------------------------------------------------------------------------

def _get_dry_run_flag() -> bool:
    """Read the current DRY_RUN value from .env."""
    env_file = Path(_env_path)
    if not env_file.exists():
        return False
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("DRY_RUN="):
            return line.split("=", 1)[1].strip().lower() == "true"
    return False


def _set_dry_run_flag(value: bool) -> None:
    """Flip the DRY_RUN line in .env in-place."""
    env_file = Path(_env_path)
    if not env_file.exists():
        return
    text = env_file.read_text(encoding="utf-8")
    new_val = "true" if value else "false"
    new_text = re.sub(
        r"^DRY_RUN=.*$",
        f"DRY_RUN={new_val}",
        text,
        flags=re.MULTILINE,
    )
    env_file.write_text(new_text, encoding="utf-8")
    logger.info("DRY_RUN toggled to %s", new_val)


@app.route("/api/dry_run/toggle", methods=["POST"])
def api_dry_run_toggle():
    _check_token()
    current = _get_dry_run_flag()
    _set_dry_run_flag(not current)
    return redirect(f"/settings?token={_secret}")


# ---------------------------------------------------------------------------
# API — errors
# ---------------------------------------------------------------------------

@app.route("/api/errors/resolve", methods=["POST"])
def api_errors_resolve():
    _check_token()
    try:
        error_id = int(request.form.get("error_id", 0))
    except (ValueError, TypeError):
        abort(400, "invalid error_id")
    db = _get_db()
    db.mark_error_resolved(error_id)
    logger.info("Error %d marked as resolved", error_id)
    return redirect(f"/settings?token={_secret}")


# ---------------------------------------------------------------------------
# API — stats (JSON for future JS refresh)
# ---------------------------------------------------------------------------

@app.route("/api/stats")
def api_stats():
    _check_token()
    db = _get_db()
    return jsonify(db.get_stats())


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return {"status": "ok", "service": "gmail-bot-review-server"}


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def start_server(db_path: str, secret: str, port: int = 8080,
                 whitelist_path: str = "", env_path: str = "") -> None:
    """
    Called from main.py in a background daemon thread.
    Sets module-level config and starts Flask.
    """
    global _db_path, _secret, _whitelist_path, _env_path
    _db_path = db_path
    _secret = secret
    _whitelist_path = whitelist_path
    _env_path = env_path

    logger.info("Dashboard starting on http://localhost:%d/?token=***", port)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    try:
        app.run(
            host="127.0.0.1",
            port=port,
            debug=False,
            use_reloader=False,
        )
    except OSError as exc:
        logger.error("Dashboard failed to bind on port %d: %s", port, exc)


def open_review_page(secret: str, port: int = 8080) -> None:
    """Open the review page in the default browser."""
    url = f"http://localhost:{port}/review?token={secret}"
    webbrowser.open(url)
    logger.info("Opened review page: %s", url.replace(secret, "***"))


def open_dashboard(secret: str, port: int = 8080) -> None:
    """Open the dashboard in the default browser."""
    url = f"http://localhost:{port}/?token={secret}"
    webbrowser.open(url)
    logger.info("Opened dashboard: %s", url.replace(secret, "***"))
