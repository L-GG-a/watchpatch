@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
    echo Please install dependencies first. See README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" examples\demo.py
if errorlevel 1 (
    echo Demo failed. See the message above.
    pause
    exit /b 1
)
pause
