@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python main.py > "%~dp0server_out3.log" 2> "%~dp0server_err3.log"
