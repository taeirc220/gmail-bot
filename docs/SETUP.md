# Gmail Automation Bot — Setup Guide

Follow these steps in order. Each step must be completed before the next.

---

## Prerequisites

- Windows 10 or 11
- A Google account (the Gmail inbox you want to monitor)
- Python 3.12 installed from [python.org](https://www.python.org/downloads/)
  - During installation: check **"Add Python to PATH"**

---

## Step 1 — Place the Project

Copy or clone the project folder to a permanent location on your PC.
Recommended: `C:\Users\YourName\gmail-bot\`

Avoid paths with spaces if possible.

---

## Step 2 — Create the Virtual Environment

Open a terminal (Command Prompt or PowerShell) in the project root:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## Step 3 — Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Click **"Select a project"** → **"New Project"**
   - Name: `GmailBot` (or anything)
   - Click **Create**

3. In the left menu → **APIs & Services** → **Library**
   - Search for **"Gmail API"**
   - Click it → click **Enable**

4. In the left menu → **APIs & Services** → **OAuth consent screen**
   - User type: **External**
   - Fill in App name: `GmailBot`, User support email: your email
   - Click **Save and Continue** through all screens
   - On **"Test users"** step: click **Add users** → add your Gmail address → **Save**

5. In the left menu → **APIs & Services** → **Credentials**
   - Click **"+ Create Credentials"** → **OAuth client ID**
   - Application type: **Desktop app**
   - Name: `GmailBot Desktop`
   - Click **Create**
   - Click **Download JSON** on the confirmation dialog
   - Rename the downloaded file to `credentials.json`
   - Move it to the project root folder

---

## Step 4 — Configure the .env File

In the project root, copy `.env.example` to `.env`:

```bat
copy .env.example .env
```

Open `.env` in a text editor and fill in:

```
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json

# Generate a random secret — run this in your terminal:
# python -c "import secrets; print(secrets.token_hex(32))"
REVIEW_SERVER_SECRET=paste_your_generated_secret_here

REVIEW_SERVER_PORT=8080
DB_PATH=data/gmail_bot.db
DRY_RUN=false
TEST_MODE=false
QUIET_HOURS_START=22
QUIET_HOURS_END=7
```

To generate the secret, run this in your terminal (with venv active):
```bat
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output and paste it as the value of `REVIEW_SERVER_SECRET`.

---

## Step 5 — First Run (OAuth2 Authentication)

Run the bot manually for the first time. A browser window will open for Google consent.

```bat
python src\main.py
```

1. A browser window opens with a Google sign-in prompt
2. Sign in with the Gmail account you want to monitor
3. Click **"Allow"** on the permissions screen
4. The browser shows "Authentication successful" — close it
5. `token.json` is created in the project root
6. The terminal shows: `Polling started. Press Ctrl+C to stop.`
7. Press **Ctrl+C** to stop for now

If the browser shows a warning about the app being unverified, click **"Advanced"** → **"Go to GmailBot (unsafe)"**. This is expected for personal/test apps.

---

## Step 6 — Test the Bot

Send yourself a test email from a different account. Wait ~90 seconds. A Windows toast notification should appear.

Check `logs/` for the daily log file to confirm the bot is running correctly.

---

## Step 7 — Set Up Auto-Start (Windows Task Scheduler)

**Edit the launcher path first:**

Open `deployment\task_scheduler.xml` in a text editor. Find the two lines marked `EDIT THIS PATH` and replace the placeholder paths with the actual path to your project folder.

Example:
```xml
<Command>C:\Users\YourName\gmail-bot\deployment\launcher.bat</Command>
<WorkingDirectory>C:\Users\YourName\gmail-bot</WorkingDirectory>
```

**Install the scheduled task:**

In a terminal (run as Administrator):
```bat
schtasks /create /xml "deployment\task_scheduler.xml" /tn "GmailBot"
```

Or import manually:
1. Open **Task Scheduler** (search in Start menu)
2. Click **"Import Task..."** in the right panel
3. Browse to `deployment\task_scheduler.xml` and open it
4. Click **OK**

**Verify:**
1. Log off Windows and log back on
2. Wait ~30 seconds (the task has a 30-second logon delay)
3. Check `logs\` for a new log file with today's date
4. Confirm the bot is polling (you should see entries in the log)

---

## Step 8 — Review Server

The review server runs automatically on `http://localhost:8080`.

To open the newsletter review page:
```
http://localhost:8080/review?token=YOUR_REVIEW_SERVER_SECRET
```

Replace `YOUR_REVIEW_SERVER_SECRET` with the value from your `.env` file.

Bookmark this URL in your browser for easy access.

---

## Updating the Config

To change what counts as "important" or add keywords:
- Edit `config\importance_rules.yaml`
- Restart the bot (Task Scheduler → right-click GmailBot → End → Run)

To whitelist a newsletter sender:
- Edit `config\newsletter_whitelist.txt`
- Add the sender email or `@domain.com`
- Restart the bot

---

## DRY RUN Mode (Safe Testing)

To test without any real deletions or unsubscribes:
```
DRY_RUN=true
```
in your `.env`. All actions are logged as `[DRY RUN]` but nothing is executed.

---

## Uninstalling

To remove the scheduled task:
```bat
schtasks /delete /tn "GmailBot" /f
```

To revoke Gmail access: go to [myaccount.google.com/permissions](https://myaccount.google.com/permissions) and remove GmailBot.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Browser doesn't open on first run | Run `python src\auth.py` directly |
| "credentials.json not found" | Make sure the file is in the project root, not a subfolder |
| No notifications appearing | Check `logs\` — look for errors. Make sure DRY_RUN=false |
| Review page shows 403 | Wrong token in the URL — check your `.env` REVIEW_SERVER_SECRET |
| Bot stops after a while | Check logs for auth errors. May need to re-run `python src\auth.py` to refresh token |
| Task Scheduler task not running | Open Task Scheduler → check task status → look at History tab |
