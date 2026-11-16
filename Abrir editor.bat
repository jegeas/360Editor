@echo off
cd /d "%~dp0"
start "" http://localhost:8361
python server.py
pause
