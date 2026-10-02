@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%~dp0venv\Scripts\python.exe" -m uvicorn web_app:app --host 127.0.0.1 --port 3020
