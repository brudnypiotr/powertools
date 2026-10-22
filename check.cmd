@echo off
REM powertools feasibility checker. No admin rights needed. Double-click or run: check.cmd -Only graph
rem Started from PowerShell 7, the inherited PSModulePath breaks Windows PowerShell 5.1 modules: reset it.
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0checks\check.ps1" %*
set RC=%ERRORLEVEL%
if not defined CI (
  echo.
  pause
)
exit /b %RC%
