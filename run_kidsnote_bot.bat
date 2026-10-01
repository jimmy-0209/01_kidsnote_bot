@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> run_log.txt
"%~dp0venv\Scripts\pythonw.exe" main.py >> run_log.txt 2>&1
echo ===== finished %date% %time% ===== >> run_log.txt
