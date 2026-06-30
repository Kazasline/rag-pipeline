@echo off
REM Index one or more source folders. Pass folder paths as arguments:
REM   run_ingest.bat "D:\MyProjects" "D:\ClientFiles"
REM
REM PYTHONUNBUFFERED flushes progress lines to ingest.log in real-time.
REM ingest.py's single-instance lock prevents double-runs.
set PYTHONUNBUFFERED=1
"%~dp0.venv\Scripts\python.exe" "%~dp0ingest.py" %* >> "%~dp0ingest.log" 2>&1
