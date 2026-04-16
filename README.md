# Gmail Automation Bot

A production-grade Python bot that monitors a Gmail inbox every 60 seconds, classifies incoming emails using multi-criteria AND logic, automatically manages newsletters, and delivers native Windows toast notifications — running silently in the background via Windows Task Scheduler with no cloud dependency and zero monthly cost.

---

## Features

- **Intelligent classification** — Emails are classified into four categories using strict AND-logic across three groups: personal emails from real humans (Group A), ticket/booking confirmations with PDF attachments (Group B), and job application responses (Group C). A single keyword match is never enough.
- **Automated newsletter handling** — Detects newsletters via `List-Unsubscribe` headers, automatically unsubscribes (HTTP or mailto) and moves them to Trash. Ambiguous cases are queued for manual review.
- **Local review UI** — A Flask server on `localhost:8080` serves a review page where you can approve, unsubscribe, or keep flagged newsletters. Secured by a token, runs as a daemon thread inside the bot process.
- **Windows toast notifications** — Native pop-up notifications (bottom-right corner) for important emails, using `plyer` + `win10toast`. Clicking opens the email directly in Gmail.
- **Quiet hours** — Suppresses notifications 22:00–07:00. Important emails that arrive overnight are batched into a single morning digest at 07:00.
- **Safety-first design** — DRY_RUN mode, bulk deletion threshold (10 emails/cycle), Trash-only (no hard-delete), sender whitelist.
- **Full audit trail** — SQLite database logs every classified email, every newsletter decision, and every error with timestamps.

---

## Tech Stack

| Purpose                  | Library                                          |
|--------------------------|--------------------------------------------------|
| Gmail API                | `google-api-python-client`                       |
| OAuth2 auth              | `google-auth-oauthlib`, `google-auth-httplib2`   |
| Email body parsing       | `beautifulsoup4` + `lxml`                        |
| Desktop notifications    | `plyer` + `win10toast`                           |
| Database                 | `sqlite3` (stdlib)                               |
| Scheduling               | `schedule`                                       |
| Config                   | `python-dotenv`, `PyYAML`                        |
| HTTP unsubscribe         | `requests`                                       |
| Review UI server         | `flask`                                          |
| Testing                  | `pytest`, `pytest-mock`                          |

---

## Architecture

```
Gmail Inbox
     │
     │  REST API polling every 60s (historyId-based, no duplicates)
     ▼
┌─────────────────────────────────────────────────────────┐
│                      main.py (orchestrator)             │
│  schedule loop ──► poll_cycle() ──► classify()          │
│                                         │               │
│               ┌─────────────────────────┤               │
│               │         │               │               │
│               ▼         ▼               ▼               │
│          Notifier  NewsletterManager  Database          │
│         (plyer)    (whitelist, safety  (SQLite)         │
│         toast       rails, unsubscribe)                 │
│               │                                         │
│               └──► Flask review server (daemon thread)  │
│                    localhost:8080/review                 │
└─────────────────────────────────────────────────────────┘
         │
         ▼
   Windows Task Scheduler
   auto-starts on login via pythonw (no console window)
```

### Classification order (first match wins)
```
Group B (tickets + PDF)  →  important/group_b
Group C (job replies)    →  important/group_c
Group A (personal email) →  important/group_a
Newsletter (List-Unsubscribe header present)
  └── no prior decision  →  newsletter/high  →  auto-unsubscribe + trash
  └── prior 'keep'       →  newsletter/low   →  queue for review
Ignored
```

---

## Safety Design

| Rail                       | Behaviour                                                        |
|----------------------------|------------------------------------------------------------------|
| `DRY_RUN=true`             | All actions logged as `[DRY RUN]`; no Gmail API writes          |
| Bulk threshold             | Pauses and notifies if >10 deletions in a single 60s cycle      |
| Trash only                 | All deletions go to Trash (30-day recovery window)              |
| Sender whitelist           | `config/newsletter_whitelist.txt` — whitelisted senders never touched |
| `TEST_MODE=true`           | Bot restricted to `BotTestInbox` label; enforced in all tests   |

---

## Setup

See [docs/SETUP.md](docs/SETUP.md) for the full step-by-step guide covering:

1. Python 3.12+ and virtual environment setup
2. Google Cloud project + Gmail API + OAuth2 credentials
3. `.env` configuration
4. First-run authentication (OAuth2 browser consent)
5. Windows Task Scheduler auto-start

---

## Running Tests

```bat
.venv\Scripts\activate
pytest -m "not integration" -v
```

129 unit tests. All tests enforce `DRY_RUN=true` and `TEST_MODE=true` via an `autouse` pytest fixture — no real Gmail API calls, no real deletions.

To run integration tests (requires live Gmail account with `BotTestInbox` label):

```bat
pytest -m integration -v
```

---

## Project Structure

```
gmail-bot/
├── src/
│   ├── main.py                  # Entry point; polling loop + orchestration
│   ├── auth.py                  # OAuth2 flow and silent token refresh
│   ├── gmail_client.py          # Gmail API wrapper (list, get, trash, send)
│   ├── classifier.py            # AND-logic email classification (pure functions)
│   ├── notifier.py              # Windows toast notifications (plyer)
│   ├── newsletter_manager.py    # Unsubscribe + trash + safety rails
│   ├── review_generator.py      # Generates the localhost review HTML page
│   ├── review_server.py         # Flask server for newsletter review UI
│   └── database.py              # SQLite interface (all DB reads/writes)
│
├── config/
│   ├── importance_rules.yaml    # All keyword lists — edit here to tune classification
│   └── newsletter_whitelist.txt # Senders/domains that are never auto-deleted
│
├── tests/                       # pytest unit tests (129 tests, 0 integration by default)
│
├── deployment/
│   ├── launcher.bat             # Activates venv and runs pythonw src\main.py
│   └── task_scheduler.xml       # Windows Task Scheduler definition (logon trigger)
│
├── docs/
│   └── SETUP.md                 # Full setup walkthrough
│
├── .env.example                 # Template for .env (secrets not committed)
└── requirements.txt
```

---

## Environment Variables

| Variable                | Description                                      | Default         |
|-------------------------|--------------------------------------------------|-----------------|
| `GMAIL_CREDENTIALS_PATH`| Path to `credentials.json`                      | `credentials.json` |
| `GMAIL_TOKEN_PATH`      | Path to `token.json`                             | `token.json`    |
| `REVIEW_SERVER_SECRET`  | Token for review UI (generate with `secrets`)   | (required)      |
| `REVIEW_SERVER_PORT`    | Flask review server port                         | `8080`          |
| `DB_PATH`               | SQLite database path                             | `data/gmail_bot.db` |
| `DRY_RUN`               | Set `true` to simulate all actions               | `false`         |
| `TEST_MODE`             | Set `true` to restrict to BotTestInbox label    | `false`         |
| `QUIET_HOURS_START`     | Quiet hours start (24h)                          | `22`            |
| `QUIET_HOURS_END`       | Quiet hours end (24h)                            | `7`             |
