@echo off
REM REYDM Desktop launcher (Windows)
cd /d "%~dp0"
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\pip install --upgrade pip
    call .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\python desktop_app.py
