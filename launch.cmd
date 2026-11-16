@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "CQ_PYTHON="
if exist ".venv\Scripts\python.exe" set "CQ_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined CQ_PYTHON if exist "..\..\work\crawler_runtime\Scripts\python.exe" set "CQ_PYTHON=%~dp0..\..\work\crawler_runtime\Scripts\python.exe"
if not defined CQ_PYTHON (
  echo Python environment missing. Run setup.cmd first.
  pause
  exit /b 2
)
"%CQ_PYTHON%" -u launcher.py %*
set "CQ_EXIT=%ERRORLEVEL%"
echo.
echo Exit code: %CQ_EXIT%
pause
exit /b %CQ_EXIT%
