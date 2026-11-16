@echo off
cd /d "%~dp0"
echo Installing requirements (first run only takes a minute)...
python -m pip install -q --no-warn-script-location -r requirements.txt
echo Opening Opera GX...
python lola_export.py --opera
pause
