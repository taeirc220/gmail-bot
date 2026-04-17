#!/bin/bash
# Gmailbot Linux launcher — used by systemd and manual starts.
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec python src/main.py
