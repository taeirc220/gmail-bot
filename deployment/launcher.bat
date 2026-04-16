@echo off
REM Gmail Automation Bot — Silent launcher
REM Uses pythonw.exe to run without a console window.
REM Called by Windows Task Scheduler on user logon.

cd /d "%~dp0.."
call .venv\Scripts\activate.bat
pythonw src\main.py
