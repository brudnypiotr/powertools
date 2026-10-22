@echo off
REM powertools feasibility checker. No admin rights needed. Double-click or run: check.cmd -Only graph
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0checks\check.ps1" %*
set RC=%ERRORLEVEL%
if not defined CI (
  echo.
  pause
)
exit /b %RC%
