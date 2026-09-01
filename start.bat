@echo off
cd /d "%~dp0"
if not exist frontend\node_modules (
  echo Install frontend dependencies first: cd frontend ^& npm install
  pause
  exit /b 1
)
cd frontend
call npm start
