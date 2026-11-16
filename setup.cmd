@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3.12 -m venv .venv
if errorlevel 1 (
  echo Install Python 3.12 first, then run this file again.
  pause
  exit /b 2
)
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
set "CQ_EXIT=%ERRORLEVEL%"
pause
exit /b %CQ_EXIT%
