@echo off
cd /d "%~dp0"
echo Installing requirements (first run only takes a minute)...
python -m pip install -q -r requirements.txt
python -m playwright install chromium
set /p START="Paste the URL of your Lola 'My courses' page and press Enter: "
python lola_export.py --start "%START%"
pause
