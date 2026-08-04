@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
where python
python -c "import sys; print(sys.executable); print(sys.version)"
