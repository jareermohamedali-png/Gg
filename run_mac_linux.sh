#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Installing requirements (first run only takes a minute)..."
python3 -m pip install -q -r requirements.txt
python3 -m playwright install chromium
python3 lola_export.py "$@"
