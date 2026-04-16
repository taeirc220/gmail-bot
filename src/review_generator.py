"""
review_generator.py — Generates the HTML review page for uncertain newsletters.

Called by review_server.py to produce the HTML served at /review.
Reads unresolved pending_review entries from the database.
"""

from src.database import Database

_STYLE = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; margin: 0; padding: 20px; color: #333; }
  h1   { color: #1a73e8; margin-bottom: 4px; }
  .subtitle { color: #666; margin-bottom: 24px; font-size: 14px; }
  .empty    { background: #fff; border-radius: 8px; padding: 40px;
              text-align: center; color: #888; }
  table { width: 100%; border-collapse: collapse; background: #fff;
          border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  thead th { background: #1a73e8; color: #fff; padding: 12px 16px;
             text-align: left; font-size: 13px; font-weight: 600; }
  tbody tr:hover { background: #f8f9fa; }
  td  { padding: 12px 16px; border-bottom: 1px solid #eee;
        font-size: 13px; vertical-align: middle; }
  td.reason { color: #888; font-size: 12px; }
  .btn { display: inline-block; padding: 6px 14px; border-radius: 4px;
         border: none; cursor: pointer; font-size: 12px; font-weight: 500;
         margin-right: 6px; text-decoration: none; }
  .btn-keep  { background: #34a853; color: #fff; }
  .btn-unsub { background: #ea4335; color: #fff; }
  .btn-trash { background: #fbbc04; color: #333; }
  .btn:hover { opacity: 0.85; }
  form { display: inline; }
</style>
"""


def generate_review_page(db: Database, secret: str) -> str:
    """
    Build and return the full HTML string for the review page.
    Each pending row gets three action buttons: Keep, Unsubscribe, Trash Only.
    """
    rows = db.get_pending_reviews(unresolved_only=True)
    count = len(rows)

    body_html = ""
    if count == 0:
        body_html = '<div class="empty">No newsletters pending review. All clear!</div>'
    else:
        rows_html = "\n".join(_render_row(row, secret) for row in rows)
        body_html = f"""
        <table>
          <thead>
            <tr>
              <th>Sender</th>
              <th>Subject</th>
              <th>Received</th>
              <th>Flag reason</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
        """

    subtitle = (
        f"{count} newsletter{'s' if count != 1 else ''} pending review"
        if count > 0 else "All clear — nothing to review"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Gmail Bot — Newsletter Review</title>
  {_STYLE}
</head>
<body>
  <h1>Gmail Bot — Newsletter Review</h1>
  <p class="subtitle">{subtitle}</p>
  {body_html}
</body>
</html>"""


def _render_row(row: dict, secret: str) -> str:
    """Render a single table row with decision buttons."""
    sender = _esc(row.get("sender") or "")
    subject = _esc(row.get("subject") or "(no subject)")
    received = _fmt_date(row.get("received_at") or "")
    reason = _esc(row.get("flag_reason") or "")
    message_id = _esc(row.get("message_id") or "")

    def _form(decision: str, label: str, css_class: str) -> str:
        return f"""
        <form method="POST" action="/api/decision?token={secret}">
          <input type="hidden" name="message_id" value="{message_id}">
          <input type="hidden" name="decision"   value="{decision}">
          <button type="submit" class="btn {css_class}">{label}</button>
        </form>"""

    return f"""
    <tr>
      <td>{sender}</td>
      <td>{subject}</td>
      <td>{received}</td>
      <td class="reason">{reason}</td>
      <td>
        {_form("keep",        "Keep",              "btn-keep")}
        {_form("unsubscribe", "Unsubscribe",        "btn-unsub")}
        {_form("trash_only",  "Trash Only",         "btn-trash")}
      </td>
    </tr>"""


def _esc(text: str) -> str:
    """Minimal HTML escaping to prevent XSS from email content."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _fmt_date(iso_str: str) -> str:
    """Format ISO-8601 UTC string to a human-readable date."""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M")
    except Exception:
        return iso_str
