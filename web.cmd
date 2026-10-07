@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not defined WATCHPATCH_DB set "WATCHPATCH_DB=%~dp0.demo\watchpatch.db"
if not defined PLAYWRIGHT_BROWSERS_PATH set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.demo\playwright-browsers"
if not exist ".demo" mkdir ".demo"
if not exist ".venv\Scripts\watchpatch.exe" (
    echo Please install dependencies first. See README.md.
    pause
    exit /b 1
)
echo Starting WatchPatch at http://127.0.0.1:8787 ...
".venv\Scripts\watchpatch.exe" web
if errorlevel 1 echo Startup failed. Please read the message above.
pause
