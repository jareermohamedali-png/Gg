#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Installing requirements (first run only takes a minute)..."
python3 -m pip install -q -r requirements.txt
python3 -m playwright install chromium
read -r -p "Paste the URL of your Lola 'My courses' page and press Enter: " START
python3 lola_export.py --start "$START"
