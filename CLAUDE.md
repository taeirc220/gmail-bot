# CLAUDE.md — Gmail Automation Bot
### Source of Truth | Version 1.2 | Last Updated: 2026-04-16

This document is the **definitive specification** for the Gmail Automation Bot project.
No functional code may be written that contradicts this file.
All architectural decisions, logic rules, safety constraints, and future plans are recorded here.
When in doubt, refer to this document first. Update it before changing any behavior.

---

## Project Overview

A Python-based Gmail automation bot that:
1. Monitors a Gmail inbox every 1 minute
2. Classifies emails as **Important**, **Newsletter**, **Unsure**, or **Ignored**
3. Sends desktop pop-up notifications for important emails
4. Automatically unsubscribes and deletes newsletters
5. Generates a local HTML review file for uncertain newsletter cases
6. Runs invisibly and persistently without manual startup

---

## Section 1 — Environment & Stack

### Operating System
- **Development & Production**: Windows 11
- Same machine for both

### Python
- **Version**: Python 3.12 (latest stable)
- **Rationale**: Best ecosystem maturity, performance, and long-term support for this use case

### Virtual Environment
- **Tool**: `venv` (built-in, zero extra dependencies)
- **Setup**:
  ```bash
  python -m venv .venv
  .venv\Scripts\activate
  pip install -r requirements.txt
  ```

### Dependency Management
- **File**: `requirements.txt` with pinned versions (`pip freeze > requirements.txt`)
- No `pyproject.toml` for v1; upgrade path exists if complexity grows

### Core Libraries

| Purpose                  | Library                                          | Rationale                              |
|--------------------------|--------------------------------------------------|----------------------------------------|
| Gmail API                | `google-api-python-client`                       | Official Google client                 |
| Auth helpers             | `google-auth-oauthlib`, `google-auth-httplib2`   | Required Google auth companions        |
| Env/config               | `python-dotenv`                                  | Simple, widely adopted                 |
| Email body parsing       | `beautifulsoup4` + `lxml`                        | Handles HTML email bodies cleanly      |
| Desktop notifications    | `plyer` + `win10toast`                           | Native Windows toast pop-ups           |
| Database                 | `sqlite3` (stdlib)                               | Built-in, zero dependencies            |
| Scheduling/polling loop  | `schedule`                                       | Lightweight, readable polling syntax   |
| Logging                  | `logging` (stdlib)                               | Built-in structured output             |
| Config files             | `PyYAML`                                         | For `importance_rules.yaml`            |
| HTTP (unsubscribe)       | `requests`                                       | Newsletter unsubscribe HTTP calls      |
| Review UI server         | `flask`                                          | Local review server on localhost:8080  |

---

## Section 2 — Deployment Architecture

**Decision: Windows PC Only — LOCKED**

### How it works
- Bot runs on the user's Windows PC as a background process
- Auto-starts silently on user logon via **Windows Task Scheduler**
- `pythonw` (no console window) runs `src/main.py` at logon
- Bot only runs while the PC is on — goes offline when shut down
- **No cloud server, no monthly cost**

```
[Your Windows PC — runs while PC is on]
  pythonw src\main.py (no console window)
  polls Gmail every 1 min
  classifies emails
  on important email → Windows toast notification (plyer)
  on newsletter review needed → opens localhost:8080/review in browser
```

### Notifications
- **Library**: `plyer` + `win10toast` — native Windows toast pop-ups (bottom-right corner)
- Click-to-open: attempts `win10toast` `callback_on_click` to open email in browser
- No external notification service required

### Review System
- A small **Flask server runs on `localhost:8080`** in a background daemon thread
- User opens `http://localhost:8080/review?token={SECRET}` to review uncertain newsletters
- Not internet-facing — local only
- Secured by a `REVIEW_SERVER_SECRET` token in `.env`

### Auto-Start
- `deployment/launcher.bat` — activates venv and runs `pythonw src\main.py`
- `deployment/task_scheduler.xml` — Task Scheduler task, triggers on logon, hidden window
- Restarts on failure up to 3 times (1-minute intervals)

### Credentials
- `credentials.json` and `token.json` in project root
- Both excluded from Git via `.gitignore`
- `.env` file holds all secrets — also excluded from Git

---

## Section 3 — Authentication & Credentials

### OAuth2 Setup
- A Google Cloud project must be created with the Gmail API enabled
- Credentials file: `credentials.json` (downloaded from Google Cloud Console)
- Full setup walkthrough: `docs/SETUP.md`

### Token Storage
- Token file: `token.json` (local filesystem, or cloud server filesystem)
- **Must be excluded from Git** via `.gitignore` — never committed, never logged
- `.env` file stores paths and secrets — also excluded from Git

### Required OAuth2 Scope
```
https://www.googleapis.com/auth/gmail.modify
```
This single scope covers: read, label, move to trash, and delete emails. Do not request broader scopes.

### Token Refresh Strategy
- **Automatic and silent** using `google-auth` library's built-in refresh mechanism
- No user interaction required for normal refresh cycles
- **First run**: if `token.json` is missing, `InstalledAppFlow` opens the browser automatically for consent (OK on Windows — browser is available)
- On unrecoverable auth failure (token revoked, requires re-consent):
  1. Log the error with full traceback to `logs/bot.log`
  2. Fire a desktop notification: `"Gmail Bot: Re-authentication required. Run setup."`
  3. Pause the polling loop until re-authentication completes

### Multi-Account
- Not supported in v1
- No account identifiers should be hardcoded; all account references go through a single config value to enable future multi-account support with minimal refactor

---

## Section 4 — Polling & Scheduling

- **Polling interval**: Every **1 minute**
- **API method**: Gmail REST API polling via `list` + `get` (not Gmail Pub/Sub)
  - Pub/Sub is a documented future upgrade path (lower latency, no polling overhead)
  - Track last-processed `historyId` to avoid reprocessing emails across poll cycles
- **Quiet hours**: 22:00 – 07:00 local time
  - Bot **continues polling and processing** during quiet hours
  - All actions (labeling, deleting, logging) execute normally
  - **Desktop notifications are suppressed** — no pop-ups fired
  - A background refresh sweep runs every **2 hours** during quiet hours
  - At 07:00, if any important emails arrived overnight, send a **digest notification**: `"Gmail Bot: X important emails while you were away"`
- **Polling loop**: Managed by the `schedule` library in `main.py`

---

## Section 5 — Importance Classification Logic

### Core Rule: AND Logic
An email is **Important** only when **all conditions within a group are simultaneously satisfied**.
A single keyword hit is never sufficient. Partial matches across groups do not combine.

### Classification Groups

#### Group A — Personal Emails (from real humans)
All of the following must be true:
- No `List-Unsubscribe` header present
- Sender address is NOT from a known commercial/bulk domain
- Sender name appears to be a personal name (not a brand, service, or auto-sender)
- Email body contains personal language (first-person pronouns, direct address to recipient)

#### Group B — Tickets & Bookings (event, travel, purchase)
All of the following must be true:
- Subject OR body contains at least one ticket keyword:
  `ticket`, `booking`, `reservation`, `order confirmation`, `e-ticket`, `boarding pass`, `seat`, `admission`
- **AND** a PDF or file attachment is present
- **AND** no `List-Unsubscribe` header present
- **AND** sender is not a known newsletter/bulk domain

#### Group C — Job Application Responses
All of the following must be true:
- Subject OR body contains at least one job keyword:
  `application`, `interview`, `position`, `vacancy`, `role`, `hiring`, `recruiter`,
  `HR`, `we received your`, `thank you for applying`, `next steps`, `job offer`
- **AND** no `List-Unsubscribe` header present
- **AND** email is not a mass-sent marketing email (no bulk headers)

### Configuration
All keyword lists live in `config/importance_rules.yaml` — **never hardcoded in logic files**.
This is the single place to tune importance detection without touching application code.

```yaml
# config/importance_rules.yaml (structure reference)
ticket_keywords: [ticket, booking, reservation, ...]
job_keywords: [application, interview, position, ...]
personal_name_exclusions: [noreply, no-reply, donotreply, support, info, newsletter]
```

### Sensitivity Target
Balanced — neither overly sensitive nor overly conservative.
The AND logic naturally controls false positives. Calibrate keyword lists against real inbox data during initial testing.

---

## Section 6 — Newsletter Detection & Management

### Detection Criteria (v1)
An email is classified as a **Newsletter candidate** if:
- The `List-Unsubscribe` header is present in raw email headers

Future detection signals (v2 backlog):
- Sender domain patterns (e.g., `@mailchimp.com`, `@substack.com`)
- Subject line patterns (e.g., `weekly digest`, `issue #`)
- Bulk-send indicators in headers

### Confidence Threshold
- **High confidence** (auto-action): `List-Unsubscribe` header present AND sender has no prior "Keep" decision in the database
- **Low confidence** (queue for review): any ambiguity — sender seen before in non-newsletter context, or only one weak signal

### Automated Action (High Confidence)
1. Attempt to **unsubscribe** via the `List-Unsubscribe` value (mailto or HTTP GET)
2. Move email to **Trash** (30-day recovery window — never hard-delete in v1)
3. Record action in `emails_processed` table in SQLite

### "Unsure" Newsletter Review Process (Low Confidence)
1. Do NOT auto-delete
2. Add email to the `pending_review` queue in the database
3. Regenerate `review/unsure_newsletters.html` with updated list
4. Send a desktop notification: `"Gmail Bot: X newsletters need your review"`
5. HTML file contains per-email rows with:
   - Sender, subject, date received, reason it was flagged
   - Two action buttons: **[Delete & Unsubscribe]** / **[Keep — Not a Newsletter]**
6. User decisions are written back to `newsletter_decisions` table
7. Future classifications for that sender are informed by this decision

### Safety Rails
- **DRY_RUN mode**: Set `DRY_RUN=true` in `.env`. All destructive actions are logged as
  `[DRY RUN] Would have deleted: {subject}` — nothing is actually executed.
  DRY_RUN is always `true` during test runs.
- **Bulk confirmation threshold**: If more than **10 emails** would be deleted in a single
  1-minute poll cycle, pause and send a desktop notification:
  `"Gmail Bot: About to delete 12 emails. Confirm in review file."` — do not proceed until
  confirmed via the review HTML.
- **Trash only**: All deletions in v1 move to Trash (recoverable). Hard-delete is not implemented.
- **Sender whitelist**: `config/newsletter_whitelist.txt` — one email address or domain per line.
  Whitelisted senders are never touched regardless of headers, forever.

---

## Section 7 — Notifications

### Primary Method: Windows Desktop Toast (Pop-up)
- Library: `plyer` (`plyer.notification.notify`)
- Style: Standard Windows toast notification (bottom-right corner, fades after ~5 seconds)
- **Clickable**: Clicking the notification opens the email directly in the default browser
  using the Gmail web URL: `https://mail.google.com/mail/u/0/#inbox/{message_id}`

### Notification Content Format
- Max **5 words** summarizing email content
- Sender name always included

| Email Type              | Format                                      | Example                                |
|-------------------------|---------------------------------------------|----------------------------------------|
| Job application response | `Job alert from {Sender Name}`              | `Job alert from Google Recruiting`     |
| Ticket / booking        | `Ticket from {Sender Name}`                 | `Ticket from Ticketmaster`             |
| Personal email          | AI-derived 5-word subject summary           | `David re: weekend plans`              |
| Newsletter review needed | `X newsletters need your review`            | `3 newsletters need your review`       |
| Auth failure            | `Re-authentication required. Run setup.`    |                                        |
| Overnight digest        | `X important emails while you were away`    | `2 important emails while you were away` |

### Persistent Sidebar (Review Mode Only)
- The "persistent side panel" is `review/unsure_newsletters.html`
- Opened by the bot via `webbrowser.open()` when the review queue has new entries
- Not a persistent background window during normal operation — review is on-demand only

### Telegram Bot
- Not implemented in v1. Retained as a future option in the backlog.

### Notification Suppression
- Quiet hours: 22:00 – 07:00 local time. No pop-ups. Processing continues normally.
- At 07:00: overnight digest fires if any important emails arrived during quiet hours.

---

## Section 8 — Error Handling & Recovery

- All errors caught at the top-level polling loop with `try/except`
- Errors logged to `logs/bot.log` with ISO timestamp and full traceback
- **Transient errors** (network timeout, API rate limit 429, temporary 5xx):
  - Silent retry with exponential backoff: 5s → 15s → 45s (max 3 retries)
  - If all retries fail: log as WARNING, skip current poll cycle, try again next minute
- **Persistent / unrecoverable errors** (auth failure, uncaught exception):
  - Log as CRITICAL
  - Send desktop notification: `"Gmail Bot: Error — check logs/bot.log"`
  - Pause polling loop; do not crash silently
- **Log rotation**: Daily log files (`bot_YYYY-MM-DD.log`), retain last 7 days, then auto-delete

---

## Section 9 — Database (SQLite)

### File
`data/gmail_bot.db` — excluded from Git

### Tables (v1 Schema)

**`emails_processed`**
```sql
id             INTEGER PRIMARY KEY AUTOINCREMENT,
message_id     TEXT UNIQUE NOT NULL,
sender         TEXT,
subject        TEXT,
received_at    DATETIME,
classification TEXT,        -- 'important' | 'newsletter' | 'unsure' | 'ignored'
action_taken   TEXT,        -- 'notified' | 'deleted' | 'queued_review' | 'none'
processed_at   DATETIME DEFAULT CURRENT_TIMESTAMP
```

**`newsletter_decisions`**
```sql
id             INTEGER PRIMARY KEY AUTOINCREMENT,
sender         TEXT,
domain         TEXT,
decision       TEXT,        -- 'delete' | 'keep'
decided_by     TEXT,        -- 'auto' | 'user'
decided_at     DATETIME DEFAULT CURRENT_TIMESTAMP
```

**`pending_review`**
```sql
id             INTEGER PRIMARY KEY AUTOINCREMENT,
message_id     TEXT UNIQUE NOT NULL,
sender         TEXT,
subject        TEXT,
received_at    DATETIME,
flag_reason    TEXT,
added_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
resolved       BOOLEAN DEFAULT FALSE
```

**`errors_log`**
```sql
id             INTEGER PRIMARY KEY AUTOINCREMENT,
timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP,
error_type     TEXT,
message        TEXT,
resolved       BOOLEAN DEFAULT FALSE
```

### Purpose
- Full audit trail of every bot action
- Powers the unsure newsletter HTML review process
- Foundation for future analytics queries
- Informs future auto-classification of known senders

---

## Section 10 — Testing Strategy

### Approach: Live Account with Safety Guards
- Tests run against the **real Gmail account** (same account the bot monitors)
- A dedicated Gmail label `BotTestInbox` is created manually before any test run
- All test fixtures seed emails into `BotTestInbox` and clean them up in teardown
- **`DRY_RUN=true` is enforced for all test runs** — no real deletions or unsubscribes
- A `TEST_MODE=true` flag in `.env.test` ensures the bot only touches emails tagged `BotTestInbox`
  and will raise an exception if it attempts to act on any other email

### Framework
- `pytest` with authenticated fixtures in `tests/conftest.py`
- Run with: `pytest tests/ --env=test`
- Test environment loaded from `.env.test` (separate from `.env`)

### Test Coverage Targets (v1)
- Importance classification logic — unit tests with sample email dicts
- Newsletter detection — unit tests with headers-present / headers-absent cases
- Bulk deletion threshold guard — assert notification fires at >10, not before
- Quiet hours suppression — assert no notification object created between 22:00–07:00
- Token refresh failure path — mock auth error, assert desktop notification triggered
- AND logic correctness — assert single-criterion emails are NOT classified as important

---

## Section 11 — Project Structure

```
gmail-bot/
├── .env                            # Secrets & runtime flags (NEVER commit)
├── .env.test                       # Test environment overrides (NEVER commit)
├── .gitignore
├── requirements.txt
├── CLAUDE.md                       # This file
│
├── config/
│   ├── importance_rules.yaml       # All keyword lists & classification config
│   └── newsletter_whitelist.txt    # Senders/domains to never touch
│
├── src/
│   ├── main.py                     # Entry point; polling loop
│   ├── auth.py                     # OAuth2 flow and silent token refresh
│   ├── gmail_client.py             # Gmail API wrapper (list, get, trash, label)
│   ├── classifier.py               # Importance & newsletter classification logic
│   ├── notifier.py                 # Desktop notification dispatch (plyer)
│   ├── newsletter_manager.py       # Unsubscribe + trash logic + whitelist check
│   ├── review_generator.py         # Generates review/unsure_newsletters.html
│   └── database.py                 # SQLite interface (all DB reads/writes here)
│
├── review/
│   └── unsure_newsletters.html     # Auto-generated; gitignored
│
├── data/
│   └── gmail_bot.db                # SQLite database; gitignored
│
├── logs/
│   └── bot_YYYY-MM-DD.log          # Rotating daily logs; gitignored
│
├── tests/
│   ├── conftest.py                 # Pytest fixtures (auth, DB, test label setup)
│   ├── test_classifier.py
│   ├── test_newsletter.py
│   ├── test_notifier.py
│   └── test_safety_rails.py
│
└── docs/
    └── SETUP.md                    # Google Cloud project + OAuth2 setup walkthrough
```

### .gitignore (required entries)
```
.env
.env.test
token.json
credentials.json
data/
logs/
review/
.venv/
__pycache__/
*.pyc
```

---

## Section 12 — Future Backlog (v2+)

Document planned features here. Do not implement in v1.

| Feature                          | Notes                                              |
|----------------------------------|----------------------------------------------------|
| Gmail Pub/Sub webhooks           | Replace polling with true push for lower latency   |
| Telegram bot notifications       | Mirror desktop alerts to Telegram                  |
| Newsletter detection v2          | Sender domain patterns, subject patterns           |
| Multi-account support            | Extend auth layer to manage multiple tokens        |
| Web dashboard                    | Flask/FastAPI local dashboard for review UI        |
| Analytics queries                | "How many newsletters deleted last month?"         |
| Email summarization via LLM      | Smarter 5-word summaries using Claude API          |
| Pub/Sub real-time classification | React to emails in <5 seconds                      |

---

## Open Decisions

| # | Question                        | Decision                              | Status      |
|---|---------------------------------|---------------------------------------|-------------|
| 1 | Deployment target               | Hetzner CX22 + ntfy.sh                | **LOCKED**  |
| 2 | Quiet hours window              | 22:00–07:00 local time                | **LOCKED**  |
| 3 | Bulk delete confirmation limit  | 10 emails per poll cycle              | **LOCKED**  |

---

*Status: PRE-DEVELOPMENT — ALL DECISIONS LOCKED. Ready to begin implementation.*
