@echo off
rem Double-click to open the tracker website. Close this window to stop it.
cd /d "%~dp0"
".venv\Scripts\python.exe" serve.py --open
pause
