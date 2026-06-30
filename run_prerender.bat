@echo off
REM Pre-render all matchable PDF pages to the local PNG cache (resumable).
set PYTHONUNBUFFERED=1
"%~dp0.venv\Scripts\python.exe" "%~dp0prerender.py" >> "%~dp0prerender.log" 2>&1
