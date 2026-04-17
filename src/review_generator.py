"""
review_generator.py — Generates all HTML pages for the Gmail Bot dashboard.

Pages:
  /           Dashboard  — stats, activity chart, recent activity
  /review     Review     — newsletter approval queue (upgraded)
  /history    History    — paginated email log
  /decisions  Decisions  — per-sender newsletter decisions
  /settings   Settings   — whitelist editor, DRY_RUN toggle, error log
"""

import math
from src.database import Database

# ---------------------------------------------------------------------------
# Shared layout helpers
# ---------------------------------------------------------------------------

_BASE_CSS = """
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f0f2f5; color: #202124; display: flex; min-height: 100vh; }

  /* ---- Sidebar ---- */
  .sidebar { width: 220px; min-height: 100vh; background: #1a73e8;
             display: flex; flex-direction: column; padding: 24px 0; flex-shrink: 0; }
  .sidebar-brand { color: #fff; font-size: 17px; font-weight: 700;
                   padding: 0 20px 4px; letter-spacing: .3px; }
  .sidebar-sub   { color: rgba(255,255,255,.65); font-size: 11px;
                   padding: 0 20px 24px; }
  .status-row    { display: flex; align-items: center; gap: 8px;
                   padding: 0 20px 20px; }
  .status-dot    { width: 8px; height: 8px; border-radius: 50%;
                   background: #34a853; flex-shrink: 0; }
  .status-dot.off { background: #ea4335; }
  .status-label  { color: rgba(255,255,255,.8); font-size: 12px; }
  .nav-section   { color: rgba(255,255,255,.5); font-size: 10px;
                   text-transform: uppercase; letter-spacing: 1px;
                   padding: 0 20px 8px; }
  .nav-link      { display: flex; align-items: center; gap: 10px;
                   padding: 10px 20px; color: rgba(255,255,255,.85);
                   text-decoration: none; font-size: 14px; border-radius: 0;
                   transition: background .15s; }
  .nav-link:hover   { background: rgba(255,255,255,.12); }
  .nav-link.active  { background: rgba(255,255,255,.2); color: #fff;
                      font-weight: 600; }
  .nav-icon { font-size: 16px; width: 20px; text-align: center; }
  .nav-badge { margin-left: auto; background: #ea4335; color: #fff;
               border-radius: 10px; font-size: 10px; padding: 1px 6px;
               font-weight: 700; }

  /* ---- Main content ---- */
  .main { flex: 1; padding: 32px; overflow-x: hidden; }
  .page-header { margin-bottom: 28px; }
  .page-title  { font-size: 24px; font-weight: 600; color: #202124; }
  .page-sub    { font-size: 14px; color: #5f6368; margin-top: 4px; }

  /* ---- Stat cards ---- */
  .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
           margin-bottom: 28px; }
  .card  { background: #fff; border-radius: 10px; padding: 20px 24px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card-value { font-size: 32px; font-weight: 700; color: #202124; }
  .card-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
  .card.blue   .card-value { color: #1a73e8; }
  .card.green  .card-value { color: #34a853; }
  .card.red    .card-value { color: #ea4335; }
  .card.yellow .card-value { color: #f29900; }

  /* ---- Panel (white box) ---- */
  .panel { background: #fff; border-radius: 10px; padding: 24px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 24px; }
  .panel-title { font-size: 15px; font-weight: 600; color: #202124;
                 margin-bottom: 16px; }

  /* ---- Table ---- */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th { background: #f8f9fa; color: #5f6368; font-weight: 600;
             font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
             padding: 10px 14px; text-align: left; border-bottom: 1px solid #e8eaed; }
  tbody td { padding: 12px 14px; border-bottom: 1px solid #f1f3f4;
             vertical-align: middle; }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: #fafafa; }

  /* ---- Badges ---- */
  .badge { display: inline-block; padding: 3px 10px; border-radius: 12px;
           font-size: 11px; font-weight: 600; white-space: nowrap; }
  .badge-important  { background: #e8f0fe; color: #1a73e8; }
  .badge-newsletter { background: #fce8e6; color: #d93025; }
  .badge-ignored    { background: #f1f3f4; color: #5f6368; }
  .badge-unsure     { background: #fef7e0; color: #f29900; }
  .badge-keep       { background: #e6f4ea; color: #137333; }
  .badge-unsubscribe { background: #fce8e6; color: #d93025; }
  .badge-trash_only { background: #fef7e0; color: #f29900; }
  .badge-auto       { background: #f1f3f4; color: #5f6368; }
  .badge-user       { background: #e8f0fe; color: #1a73e8; }
  .badge-dry-run    { background: #fef7e0; color: #f29900; }
  .badge-live       { background: #e6f4ea; color: #137333; }

  /* ---- Buttons ---- */
  .btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 16px;
         border-radius: 6px; border: none; cursor: pointer; font-size: 13px;
         font-weight: 500; text-decoration: none; transition: opacity .15s; }
  .btn:hover { opacity: .85; }
  .btn-primary { background: #1a73e8; color: #fff; }
  .btn-green   { background: #34a853; color: #fff; }
  .btn-red     { background: #ea4335; color: #fff; }
  .btn-yellow  { background: #fbbc04; color: #333; }
  .btn-ghost   { background: #f1f3f4; color: #202124; }
  .btn-sm { padding: 4px 10px; font-size: 12px; }
  form { display: inline; }

  /* ---- Form elements ---- */
  .input-row { display: flex; gap: 10px; margin-bottom: 12px; }
  input[type=text] { flex: 1; padding: 8px 12px; border: 1px solid #dadce0;
                     border-radius: 6px; font-size: 13px; outline: none; }
  input[type=text]:focus { border-color: #1a73e8; }
  select { padding: 7px 12px; border: 1px solid #dadce0; border-radius: 6px;
           font-size: 13px; background: #fff; cursor: pointer; }

  /* ---- Activity feed ---- */
  .feed-item { display: flex; align-items: center; gap: 12px;
               padding: 10px 0; border-bottom: 1px solid #f1f3f4; }
  .feed-item:last-child { border-bottom: none; }
  .feed-sender { font-weight: 500; font-size: 13px; flex: 1; min-width: 0;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .feed-time   { font-size: 11px; color: #9aa0a6; flex-shrink: 0; }

  /* ---- Review cards ---- */
  .review-card { background: #fff; border-radius: 10px; padding: 18px 20px;
                 box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 14px;
                 display: flex; align-items: flex-start; gap: 16px; }
  .review-card-body { flex: 1; min-width: 0; }
  .review-sender   { font-weight: 600; font-size: 14px; margin-bottom: 3px;
                     white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .review-subject  { font-size: 13px; color: #3c4043; margin-bottom: 3px;
                     white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .review-meta     { font-size: 12px; color: #9aa0a6; }
  .review-actions  { display: flex; gap: 8px; align-items: center; flex-shrink: 0; }
  .review-checkbox { margin-top: 4px; width: 16px; height: 16px; cursor: pointer; }
  .prior-hint      { font-size: 11px; color: #9aa0a6; padding: 2px 8px;
                     background: #f1f3f4; border-radius: 10px; margin-top: 4px;
                     display: inline-block; }

  /* ---- Pagination ---- */
  .pagination { display: flex; align-items: center; gap: 8px;
                justify-content: center; margin-top: 20px; }
  .page-btn   { padding: 6px 14px; border-radius: 6px; border: 1px solid #dadce0;
                background: #fff; font-size: 13px; cursor: pointer;
                text-decoration: none; color: #202124; }
  .page-btn.active { background: #1a73e8; color: #fff; border-color: #1a73e8; }
  .page-btn:hover:not(.active) { background: #f1f3f4; }
  .page-info  { font-size: 13px; color: #5f6368; }

  /* ---- Filter bar ---- */
  .filter-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
  .filter-bar label { font-size: 13px; color: #5f6368; }

  /* ---- Settings sections ---- */
  .setting-row { display: flex; align-items: center; justify-content: space-between;
                 padding: 14px 0; border-bottom: 1px solid #f1f3f4; }
  .setting-row:last-child { border-bottom: none; }
  .setting-label { font-size: 14px; font-weight: 500; }
  .setting-desc  { font-size: 12px; color: #5f6368; margin-top: 2px; }
  .whitelist-entry { display: flex; align-items: center; gap: 12px;
                     padding: 8px 0; border-bottom: 1px solid #f1f3f4; }
  .whitelist-entry:last-of-type { border-bottom: none; }
  .wl-text { font-size: 13px; font-family: monospace; flex: 1; }
  .empty-state { text-align: center; padding: 40px 20px; color: #9aa0a6; font-size: 14px; }
</style>
"""

_CHARTJS_CDN = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>'


def _layout(page: str, content: str, secret: str, stats: dict | None = None) -> str:
    """Wrap content in the shared sidebar + main layout."""
    pending = stats["pending_reviews"] if stats else 0
    errors  = stats["unresolved_errors"] if stats else 0

    def _nav(label: str, icon: str, href: str, badge: int = 0) -> str:
        active = "active" if page == label else ""
        badge_html = f'<span class="nav-badge">{badge}</span>' if badge > 0 else ""
        return (f'<a class="nav-link {active}" href="{href}?token={secret}">'
                f'<span class="nav-icon">{icon}</span>{label}{badge_html}</a>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Gmail Bot — {page}</title>
  {_BASE_CSS}
  {_CHARTJS_CDN}
</head>
<body>
  <nav class="sidebar">
    <div class="sidebar-brand">Gmail Bot</div>
    <div class="sidebar-sub">Local dashboard</div>
    <div class="status-row">
      <div class="status-dot"></div>
      <span class="status-label">Running</span>
    </div>
    <div class="nav-section">Pages</div>
    {_nav("Dashboard", "📊", "/")}
    {_nav("Review", "📋", "/review", pending)}
    {_nav("History", "📜", "/history")}
    {_nav("Decisions", "🗂️", "/decisions")}
    {_nav("Settings", "⚙️", "/settings", errors)}
  </nav>
  <main class="main">
    {content}
  </main>
</body>
</html>"""


def _esc(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;"))


def _fmt_date(iso_str: str) -> str:
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M")
    except Exception:
        return iso_str or ""


def _time_ago(iso_str: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = int((now - dt).total_seconds())
        if diff < 60:
            return f"{diff}s ago"
        if diff < 3600:
            return f"{diff // 60}m ago"
        if diff < 86400:
            return f"{diff // 3600}h ago"
        return f"{diff // 86400}d ago"
    except Exception:
        return ""


def _badge(css_class: str, text: str) -> str:
    return f'<span class="badge badge-{css_class}">{_esc(text)}</span>'


# ---------------------------------------------------------------------------
# Page 1 — Dashboard
# ---------------------------------------------------------------------------

def generate_dashboard(db: Database, secret: str) -> str:
    stats = db.get_stats()
    recent = db.get_recent_emails(limit=10)
    chart_data = db.get_activity_by_day(days=7)

    # Stat cards
    cards = f"""
    <div class="cards">
      <div class="card blue">
        <div class="card-value">{stats['total_processed']}</div>
        <div class="card-label">Emails processed</div>
      </div>
      <div class="card green">
        <div class="card-value">{stats['total_important']}</div>
        <div class="card-label">Important emails</div>
      </div>
      <div class="card red">
        <div class="card-value">{stats['total_newsletters']}</div>
        <div class="card-label">Newsletters handled</div>
      </div>
      <div class="card yellow">
        <div class="card-value">{stats['pending_reviews']}</div>
        <div class="card-label">Pending reviews</div>
      </div>
    </div>"""

    # Chart data for Chart.js
    labels = [r["date"] for r in chart_data]
    imp_data    = [r["important"]  for r in chart_data]
    news_data   = [r["newsletter"] for r in chart_data]
    ign_data    = [r["ignored"]    for r in chart_data]
    unsure_data = [r["unsure"]     for r in chart_data]

    chart_panel = f"""
    <div class="panel">
      <div class="panel-title">Activity — last 7 days</div>
      <canvas id="activityChart" height="80"></canvas>
      <script>
        new Chart(document.getElementById('activityChart'), {{
          type: 'bar',
          data: {{
            labels: {labels},
            datasets: [
              {{ label: 'Important',  data: {imp_data},    backgroundColor: '#1a73e8' }},
              {{ label: 'Newsletter', data: {news_data},   backgroundColor: '#ea4335' }},
              {{ label: 'Ignored',    data: {ign_data},    backgroundColor: '#dadce0' }},
              {{ label: 'Unsure',     data: {unsure_data}, backgroundColor: '#fbbc04' }}
            ]
          }},
          options: {{
            responsive: true, plugins: {{ legend: {{ position: 'top' }} }},
            scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, beginAtZero: true }} }}
          }}
        }});
      </script>
    </div>"""

    # Recent activity feed
    if recent:
        feed_items = "\n".join(
            f'<div class="feed-item">'
            f'  <div class="feed-sender">{_esc(r["sender"] or "(unknown)")}</div>'
            f'  {_badge(r["classification"], r["classification"])}'
            f'  <span class="feed-time">{_time_ago(r["processed_at"] or "")}</span>'
            f'</div>'
            for r in recent
        )
    else:
        feed_items = '<div class="empty-state">No emails processed yet.</div>'

    today_badge = (f'<span class="badge badge-important">{stats["today_processed"]} today</span>'
                   if stats["today_processed"] > 0 else "")

    feed_panel = f"""
    <div class="panel">
      <div class="panel-title">Recent activity {today_badge}</div>
      {feed_items}
    </div>"""

    content = f"""
    <div class="page-header">
      <div class="page-title">Dashboard</div>
      <div class="page-sub">Overview of Gmail Bot activity</div>
    </div>
    {cards}
    {chart_panel}
    {feed_panel}"""

    return _layout("Dashboard", content, secret, stats)


# ---------------------------------------------------------------------------
# Page 2 — Review Queue
# ---------------------------------------------------------------------------

def generate_review_page(db: Database, secret: str) -> str:
    rows = db.get_pending_reviews(unresolved_only=True)
    count = len(rows)
    stats = db.get_stats()

    if count == 0:
        body = '<div class="empty-state">No newsletters pending review. All clear! ✓</div>'
    else:
        cards_html = "\n".join(_render_review_card(row, db, secret) for row in rows)
        body = f"""
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
          <label style="font-size:13px;color:#5f6368;">
            <input type="checkbox" id="selectAll" onchange="toggleAll(this)"
                   style="margin-right:6px;">
            Select all
          </label>
          <select id="bulkDecision" style="margin-left:auto;">
            <option value="">Bulk action...</option>
            <option value="keep">Keep all selected</option>
            <option value="unsubscribe">Unsubscribe all selected</option>
            <option value="trash_only">Trash only all selected</option>
          </select>
          <button class="btn btn-primary btn-sm" onclick="applyBulk()">Apply</button>
        </div>
        {cards_html}
        <script>
          function toggleAll(cb) {{
            document.querySelectorAll('.review-checkbox').forEach(c => c.checked = cb.checked);
          }}
          function applyBulk() {{
            const dec = document.getElementById('bulkDecision').value;
            if (!dec) {{ alert('Choose a bulk action first.'); return; }}
            const ids = Array.from(document.querySelectorAll('.review-checkbox:checked'))
                             .map(c => c.dataset.id);
            if (!ids.length) {{ alert('No items selected.'); return; }}
            if (!confirm('Apply "' + dec + '" to ' + ids.length + ' item(s)?')) return;
            const form = document.createElement('form');
            form.method = 'POST'; form.action = '/api/decision/bulk?token={secret}';
            ids.forEach(id => {{
              const i = document.createElement('input');
              i.type = 'hidden'; i.name = 'message_ids'; i.value = id;
              form.appendChild(i);
            }});
            const d = document.createElement('input');
            d.type = 'hidden'; d.name = 'decision'; d.value = dec;
            form.appendChild(d);
            document.body.appendChild(form); form.submit();
          }}
        </script>"""

    content = f"""
    <div class="page-header">
      <div class="page-title">Review Queue</div>
      <div class="page-sub">{count} newsletter{'s' if count != 1 else ''} awaiting your decision</div>
    </div>
    {body}"""

    return _layout("Review", content, secret, stats)


def _render_review_card(row: dict, db: Database, secret: str) -> str:
    sender   = _esc(row.get("sender") or "")
    subject  = _esc(row.get("subject") or "(no subject)")
    received = _fmt_date(row.get("received_at") or "")
    reason   = _esc(row.get("flag_reason") or "")
    msg_id   = _esc(row.get("message_id") or "")

    # Prior decision hint
    prior = db.get_sender_decision(row.get("sender") or "")
    hint = f'<span class="prior-hint">Prior decision: {_esc(prior)}</span>' if prior else ""

    def _btn(decision: str, label: str, css: str) -> str:
        return (f'<form method="POST" action="/api/decision?token={secret}">'
                f'<input type="hidden" name="message_id" value="{msg_id}">'
                f'<input type="hidden" name="decision"   value="{decision}">'
                f'<button type="submit" class="btn {css} btn-sm">{label}</button>'
                f'</form>')

    return f"""
    <div class="review-card">
      <input type="checkbox" class="review-checkbox" data-id="{msg_id}">
      <div class="review-card-body">
        <div class="review-sender">{sender}</div>
        <div class="review-subject">{subject}</div>
        <div class="review-meta">{received} · {reason}</div>
        {hint}
      </div>
      <div class="review-actions">
        {_btn("keep",        "Keep",        "btn-green")}
        {_btn("unsubscribe", "Unsubscribe", "btn-red")}
        {_btn("trash_only",  "Trash Only",  "btn-yellow")}
      </div>
    </div>"""


# ---------------------------------------------------------------------------
# Page 3 — Email History
# ---------------------------------------------------------------------------

def generate_history_page(db: Database, secret: str,
                          page: int = 1, classification: str = "all") -> str:
    per_page = 25
    rows, total = db.get_emails_page(page=page, per_page=per_page,
                                      classification=classification)
    total_pages = max(1, math.ceil(total / per_page))
    stats = db.get_stats()

    # Filter bar
    options = [("all", "All"), ("important", "Important"),
               ("newsletter", "Newsletter"), ("ignored", "Ignored"), ("unsure", "Unsure")]
    opts_html = "".join(
        f'<option value="{v}" {"selected" if v == classification else ""}>{l}</option>'
        for v, l in options
    )
    filter_bar = f"""
    <div class="filter-bar">
      <label>Filter:</label>
      <select id="classFilter" onchange="applyFilter(this.value)">
        {opts_html}
      </select>
      <span class="page-info" style="margin-left:auto;">{total} total</span>
    </div>
    <script>
      function applyFilter(val) {{
        window.location = '/history?token={secret}&classification=' + val + '&page=1';
      }}
    </script>"""

    # Table rows
    if rows:
        tr_html = "\n".join(
            f"""<tr>
              <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;
                         white-space:nowrap;">{_esc(r["sender"] or "")}</td>
              <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;
                         white-space:nowrap;">{_esc(r["subject"] or "(no subject)")}</td>
              <td>{_badge(r["classification"], r["classification"])}</td>
              <td style="color:#5f6368;font-size:12px;">{_esc(r["action_taken"] or "")}</td>
              <td style="color:#9aa0a6;font-size:12px;white-space:nowrap;">
                {_fmt_date(r["processed_at"] or "")}</td>
            </tr>"""
            for r in rows
        )
        table = f"""
        <table>
          <thead><tr>
            <th>Sender</th><th>Subject</th><th>Classification</th>
            <th>Action</th><th>Processed</th>
          </tr></thead>
          <tbody>{tr_html}</tbody>
        </table>"""
    else:
        table = '<div class="empty-state">No emails match this filter.</div>'

    # Pagination
    def _plink(p: int, label: str, active: bool = False) -> str:
        css = "active" if active else ""
        return (f'<a class="page-btn {css}" '
                f'href="/history?token={secret}&classification={classification}&page={p}">'
                f'{label}</a>')

    pg_links = []
    if page > 1:
        pg_links.append(_plink(page - 1, "← Prev"))
    for p in range(max(1, page - 2), min(total_pages + 1, page + 3)):
        pg_links.append(_plink(p, str(p), p == page))
    if page < total_pages:
        pg_links.append(_plink(page + 1, "Next →"))

    pagination = (f'<div class="pagination">{"".join(pg_links)}'
                  f'<span class="page-info">Page {page} of {total_pages}</span></div>'
                  if total_pages > 1 else "")

    content = f"""
    <div class="page-header">
      <div class="page-title">Email History</div>
      <div class="page-sub">All emails processed by the bot</div>
    </div>
    <div class="panel">
      {filter_bar}
      {table}
      {pagination}
    </div>"""

    return _layout("History", content, secret, stats)


# ---------------------------------------------------------------------------
# Page 4 — Newsletter Decisions
# ---------------------------------------------------------------------------

def generate_decisions_page(db: Database, secret: str) -> str:
    decisions = db.get_all_decisions()
    stats = db.get_stats()

    if decisions:
        tr_html = "\n".join(
            f"""<tr>
              <td style="font-family:monospace;font-size:12px;">{_esc(d["sender"] or "")}</td>
              <td style="color:#5f6368;font-size:12px;">{_esc(d["domain"] or "")}</td>
              <td>{_badge(d["decision"], d["decision"].replace("_", " ").title())}</td>
              <td>{_badge(d["decided_by"], d["decided_by"].title())}</td>
              <td style="color:#9aa0a6;font-size:12px;white-space:nowrap;">
                {_fmt_date(d["decided_at"] or "")}</td>
            </tr>"""
            for d in decisions
        )
        table = f"""
        <table>
          <thead><tr>
            <th>Sender</th><th>Domain</th><th>Decision</th>
            <th>Decided by</th><th>Date</th>
          </tr></thead>
          <tbody>{tr_html}</tbody>
        </table>"""
    else:
        table = '<div class="empty-state">No decisions recorded yet.</div>'

    content = f"""
    <div class="page-header">
      <div class="page-title">Newsletter Decisions</div>
      <div class="page-sub">History of per-sender keep/unsubscribe decisions</div>
    </div>
    <div class="panel">
      {table}
    </div>"""

    return _layout("Decisions", content, secret, stats)


# ---------------------------------------------------------------------------
# Page 5 — Settings
# ---------------------------------------------------------------------------

def generate_settings_page(db: Database, secret: str,
                            whitelist_entries: list[str],
                            dry_run: bool) -> str:
    stats = db.get_stats()
    errors = db.get_recent_errors(limit=20)

    dry_badge = _badge("dry-run", "DRY RUN") if dry_run else _badge("live", "LIVE")
    toggle_label = "Switch to LIVE" if dry_run else "Switch to DRY RUN"

    # Whitelist editor
    if whitelist_entries:
        wl_rows = "\n".join(
            f"""<div class="whitelist-entry">
              <span class="wl-text">{_esc(e)}</span>
              <form method="POST" action="/api/whitelist/remove?token={secret}">
                <input type="hidden" name="entry" value="{_esc(e)}">
                <button type="submit" class="btn btn-red btn-sm">Remove</button>
              </form>
            </div>"""
            for e in sorted(whitelist_entries)
        )
    else:
        wl_rows = '<div style="font-size:13px;color:#9aa0a6;padding:12px 0;">No whitelist entries yet.</div>'

    whitelist_panel = f"""
    <div class="panel">
      <div class="panel-title">Sender Whitelist</div>
      <p style="font-size:12px;color:#5f6368;margin-bottom:16px;">
        Whitelisted senders are never auto-deleted. Add exact email addresses
        or <code>@domain.com</code> for entire domains.
      </p>
      {wl_rows}
      <form method="POST" action="/api/whitelist/add?token={secret}"
            style="margin-top:14px;">
        <div class="input-row">
          <input type="text" name="entry" placeholder="user@example.com or @domain.com" required>
          <button type="submit" class="btn btn-primary">Add</button>
        </div>
      </form>
    </div>"""

    # DRY_RUN toggle
    mode_panel = f"""
    <div class="panel">
      <div class="panel-title">Bot Mode</div>
      <div class="setting-row">
        <div>
          <div class="setting-label">DRY RUN mode {dry_badge}</div>
          <div class="setting-desc">When enabled, all actions are logged but no emails are
            moved or unsubscribed.</div>
        </div>
        <form method="POST" action="/api/dry_run/toggle?token={secret}">
          <button type="submit" class="btn {'btn-green' if dry_run else 'btn-yellow'}">
            {toggle_label}
          </button>
        </form>
      </div>
    </div>"""

    # Error log
    if errors:
        err_rows = "\n".join(
            f"""<tr>
              <td style="color:#9aa0a6;font-size:12px;white-space:nowrap;">
                {_fmt_date(e["timestamp"] or "")}</td>
              <td><span class="badge badge-newsletter">{_esc(e["error_type"] or "")}</span></td>
              <td style="font-size:12px;">{_esc((e["message"] or "")[:120])}</td>
              <td>{"✓" if e["resolved"] else
                   f'<form method="POST" action="/api/errors/resolve?token={secret}">'
                   f'<input type="hidden" name="error_id" value="{e["id"]}">'
                   f'<button type="submit" class="btn btn-ghost btn-sm">Resolve</button>'
                   f'</form>'}</td>
            </tr>"""
            for e in errors
        )
        err_table = f"""
        <table>
          <thead><tr>
            <th>Time</th><th>Type</th><th>Message</th><th>Action</th>
          </tr></thead>
          <tbody>{err_rows}</tbody>
        </table>"""
    else:
        err_table = '<div class="empty-state">No errors logged. All systems healthy ✓</div>'

    errors_panel = f"""
    <div class="panel">
      <div class="panel-title">Error Log</div>
      {err_table}
    </div>"""

    content = f"""
    <div class="page-header">
      <div class="page-title">Settings</div>
      <div class="page-sub">Manage whitelist, bot mode, and view errors</div>
    </div>
    {mode_panel}
    {whitelist_panel}
    {errors_panel}"""

    return _layout("Settings", content, secret, stats)
