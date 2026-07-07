@echo off
cd /d "E:\Python\OpenCode\FreelancerStudio"
call .venv\Scripts\activate.bat
python -u main.py > "E:\Python\OpenCode\FreelancerStudio\server_log_stdout.log" 2> "E:\Python\OpenCode\FreelancerStudio\server_log_stderr.log"
