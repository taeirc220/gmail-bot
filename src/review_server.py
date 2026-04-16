"""
review_server.py — Local Flask server for newsletter review UI.

Runs on localhost:8080 in a background daemon thread inside main.py.
Not internet-facing — local machine only.
Protected by REVIEW_SERVER_SECRET token in the URL query string.

Endpoints:
  GET  /review?token=SECRET         → renders the review page
  POST /api/decision?token=SECRET   → accepts a user decision, updates DB
"""

import hmac
import logging
import os
import webbrowser

from flask import Flask, abort, redirect, request

from src.database import Database
from src.review_generator import generate_review_page

logger = logging.getLogger(__name__)

app = Flask(__name__)

# These are set by start_server() before the Flask app is started
_db_path: str = ""
_secret: str = ""


def _get_db() -> Database:
    return Database(_db_path)


def _check_token() -> None:
    """Abort with 403 if the token query parameter does not match the secret."""
    token = request.args.get("token", "")
    if not hmac.compare_digest(token, _secret):
        logger.warning("Rejected request with invalid token from %s", request.remote_addr)
        abort(403)


@app.route("/review")
def review():
    _check_token()
    db = _get_db()
    html = generate_review_page(db, _secret)
    return html


@app.route("/api/decision", methods=["POST"])
def decision():
    _check_token()

    message_id = request.form.get("message_id", "").strip()
    dec = request.form.get("decision", "").strip()

    if not message_id:
        abort(400, "message_id is required")

    valid_decisions = {"keep", "unsubscribe", "trash_only"}
    if dec not in valid_decisions:
        abort(400, f"decision must be one of: {', '.join(valid_decisions)}")

    db = _get_db()
    db.resolve_pending(message_id, dec)

    logger.info("Review decision recorded: %s → %s", message_id, dec)
    return redirect(f"/review?token={_secret}")


@app.route("/health")
def health():
    """Simple health check — no token required."""
    return {"status": "ok", "service": "gmail-bot-review-server"}


def start_server(db_path: str, secret: str, port: int = 8080) -> None:
    """
    Called from main.py in a background daemon thread.
    Sets the module-level config and starts Flask.
    """
    global _db_path, _secret
    _db_path = db_path
    _secret = secret

    logger.info("Review server starting on http://localhost:%d/review?token=***", port)

    # Silence Flask's default request logger to keep bot logs clean
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
    )


def open_review_page(secret: str, port: int = 8080) -> None:
    """Open the review page in the default browser."""
    url = f"http://localhost:{port}/review?token={secret}"
    webbrowser.open(url)
    logger.info("Opened review page: %s", url.replace(secret, "***"))
