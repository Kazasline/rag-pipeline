@echo off
rem PHASE 0 — read-only machine inspection. Installs nothing, modifies nothing.
cd /d "%~dp0"
..\.venv\Scripts\python.exe -m alirag.cli inspect
pause
