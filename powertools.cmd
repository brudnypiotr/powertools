@echo off
rem powertools: double-click (or the shortcut). Runs the setup when needed, then starts the app in the background.
rem   powertools.cmd --console     run in this window with the log visible (for debugging)
rem   powertools.cmd --setup       run the setup again
rem Started from PowerShell 7, the inherited PSModulePath breaks Windows PowerShell 5.1 modules: reset it.
set "PSModulePath="
setlocal
cd /d "%~dp0"
set "BOOT=import sys, runpy; sys.path.insert(0, r'%~dp0.'); runpy.run_module('powertools', run_name='__main__', alter_sys=True)"

if /i "%~1"=="--setup" goto setup
if not exist ".venv\Scripts\pythonw.exe" goto setup
fc /b "setup\version.txt" "local\setup-version.txt" >nul 2>&1 || goto setup
goto run

:setup
echo Setting up powertools (first start or new version) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup failed. See setup.log in this folder.
  pause
  exit /b 1
)
if /i "%~1"=="--setup" exit /b 0

:run
if /i "%~1"=="--console" (
  ".venv\Scripts\python.exe" -c "%BOOT%" %*
  pause
  exit /b
)
start "" ".venv\Scripts\pythonw.exe" -c "%BOOT%" %*
exit /b 0
