# Gmailbot — Oracle Cloud Free Tier Setup Guide

This guide walks you through deploying Gmailbot on an Oracle Cloud Always Free
instance so it runs 24/7, even when your Windows PC is off.

---

## What you'll need

- An Oracle Cloud account (free — no credit card charges for Always Free resources)
- The ntfy app on your phone (Android/iOS — push notifications from the cloud bot)
- About 30 minutes

---

## Step 1 — Create your Oracle Cloud instance

1. Sign up at **cloud.oracle.com** (choose your home region carefully — you can't change it later)
2. Go to **Compute → Instances → Create Instance**
3. Choose:
   - **Image:** Ubuntu 22.04 (Canonical)
   - **Shape:** VM.Standard.A1.Flex (Ampere ARM — Always Free)
   - **OCPUs:** 2 · **Memory:** 12 GB (well within Always Free limits)
4. Under **Networking**, make sure a public IP is assigned
5. Under **Add SSH keys**, upload your public key (or download the generated private key)
6. Click **Create**

Wait ~2 minutes for the instance to reach Running state. Note the **Public IP address**.

---

## Step 2 — Open port 8080 (Oracle's double firewall)

Oracle has TWO firewalls — you must open the port in both.

### 2a — VCN Security List (Oracle cloud firewall)
1. In Oracle Cloud Console, go to **Networking → Virtual Cloud Networks**
2. Click your VCN → **Security Lists** → **Default Security List**
3. **Add Ingress Rule:**
   - Source CIDR: `0.0.0.0/0`
   - Protocol: TCP
   - Destination Port: `8080`
4. Save

### 2b — OS firewall (inside the instance)
SSH into your instance, then run:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save
```
(Ubuntu 22.04 on Oracle uses iptables, not ufw, by default.)

---

## Step 3 — SSH into your instance

```bash
ssh ubuntu@YOUR_ORACLE_PUBLIC_IP
```

---

## Step 4 — Install Python and clone the repo

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv git
git clone https://github.com/taeirc220/gmail-bot.git /opt/gmailbot
cd /opt/gmailbot
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.server.txt
```

---

## Step 5 — Copy your credentials from Windows

On your **Windows PC** (in PowerShell):
```powershell
$ip = "YOUR_ORACLE_PUBLIC_IP"
scp "C:\Users\taeir\OneDrive\Desktop\gmail bot\credentials.json" ubuntu@${ip}:/opt/gmailbot/
scp "C:\Users\taeir\OneDrive\Desktop\gmail bot\token.json"       ubuntu@${ip}:/opt/gmailbot/
scp "C:\Users\taeir\OneDrive\Desktop\gmail bot\.env"             ubuntu@${ip}:/opt/gmailbot/
```

---

## Step 6 — Set up ntfy push notifications

1. Install the **ntfy** app on your phone:
   - Android: [Play Store](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
   - iOS: [App Store](https://apps.apple.com/app/ntfy/id1625396347)
2. In the app, tap **+** and subscribe to a topic. Pick something secret and unique,
   like `gmailbot-taeir-7x3k` (anyone who knows this string can receive your notifications).
3. On the server, edit `.env`:
   ```bash
   nano /opt/gmailbot/.env
   ```
   Add/update these lines:
   ```
   NTFY_TOPIC=gmailbot-taeir-7x3k    # ← your topic name
   NTFY_URL=https://ntfy.sh
   DASHBOARD_HOST=0.0.0.0
   DRY_RUN=false
   ```
   Save with Ctrl+O, exit with Ctrl+X.

---

## Step 7 — Create a dedicated user and install the systemd service

```bash
sudo useradd -r -s /bin/false gmailbot
sudo chown -R gmailbot:gmailbot /opt/gmailbot
sudo cp /opt/gmailbot/deployment/gmailbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gmailbot
sudo systemctl start gmailbot
```

Check it started correctly:
```bash
sudo systemctl status gmailbot
sudo journalctl -u gmailbot -f   # live logs (Ctrl+C to exit)
```

---

## Step 8 — Access the dashboard

Open this URL in any browser (phone, PC, anywhere):
```
http://YOUR_ORACLE_PUBLIC_IP:8080/?token=YOUR_REVIEW_SERVER_SECRET
```
Your `REVIEW_SERVER_SECRET` is in `.env`.

---

## Step 9 — Verify end-to-end

1. Send an email to your Gmail inbox
2. Within 90 seconds, you should receive an ntfy push notification on your phone
3. Open the dashboard URL — you should see the email in the activity feed

---

## Day-to-day commands (SSH into server)

```bash
sudo systemctl status gmailbot      # is it running?
sudo journalctl -u gmailbot -n 50   # last 50 log lines
sudo systemctl restart gmailbot     # restart after config change
sudo systemctl stop gmailbot        # stop the bot
```

---

## Updating the bot

```bash
cd /opt/gmailbot
git pull origin main
sudo systemctl restart gmailbot
```
