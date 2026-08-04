@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -u main.py > "%~dp0server_log_stdout.log" 2> "%~dp0server_log_stderr.log"
