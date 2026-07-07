@echo off
cd /d "E:\Python\OpenCode\FreelancerStudio"
call .venv\Scripts\activate.bat
python main.py > "E:\Python\OpenCode\FreelancerStudio\server_out3.log" 2> "E:\Python\OpenCode\FreelancerStudio\server_err3.log"
