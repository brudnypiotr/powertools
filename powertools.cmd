@echo off
rem powertools: double-click (or the shortcut). Runs the setup when needed, then starts the app in the background.
rem   powertools.cmd --console     run in this window with the log visible (for debugging)
rem   powertools.cmd --setup       run the setup again
rem The setup (connecting a ZIP copy to git) or an update may replace this file while it runs, and cmd reads a running
rem script from disk by byte offset: it would go on in the middle of a line. So it runs from a copy in %TEMP%.
if defined PT_REPO goto copied
set "PT_REPO=%~dp0"
rem one copy per folder (two folders may hold different versions): C:\Users\me\powertools\ -> C-Users-me-powertools-
set "PT_NAME=%~dp0"
set "PT_NAME=%PT_NAME::=%"
set "PT_NAME=%PT_NAME:\=-%"
set "PT_NAME=%PT_NAME: =_%"
set "PT_COPY=%TEMP%\powertools-%PT_NAME%.cmd"
copy /y "%~f0" "%PT_COPY%" >nul || goto copied
"%PT_COPY%" %*

:copied
rem Started from PowerShell 7, the inherited PSModulePath breaks Windows PowerShell 5.1 modules: reset it.
set "PSModulePath="
setlocal
rem (only when the copy failed: then this is the original file and %~dp0 is the app folder)
if not defined PT_REPO set "PT_REPO=%~dp0"
cd /d "%PT_REPO%"
set "BOOT=import sys, runpy; sys.path.insert(0, r'%PT_REPO%.'); runpy.run_module('powertools', run_name='__main__', alter_sys=True)"
set "SETUP=%PT_REPO%setup\setup.ps1"
rem not inherited by the app: a restart after Update starts this launcher fresh (and copies it again)
set "PT_REPO="

if /i "%~1"=="--setup" goto setup
if not exist ".venv\Scripts\pythonw.exe" goto setup
fc /b "setup\version.txt" "local\setup-version.txt" >nul 2>&1 || goto setup
goto run

:setup
echo Setting up powertools (first start or new version) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
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
