@echo off
cd /d "E:\Python\OpenCode\FreelancerStudio"
call .venv\Scripts\activate.bat
where python
python -c "import sys; print(sys.executable); print(sys.version)"
