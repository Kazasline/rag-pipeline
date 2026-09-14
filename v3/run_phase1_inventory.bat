@echo off
rem PHASE 1 — read-only inventory of the source roots (default E:\).
rem Originals are opened read-only for hashing; only the manifest DB inside
rem the ALI_RAG workspace is written.
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
..\.venv\Scripts\python.exe -m alirag.cli inventory
pause
