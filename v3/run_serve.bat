@echo off
rem Start the local API (127.0.0.1:8642). Keep this window open; the engine
rem stays warm across queries (§11).
cd /d "%~dp0"
..\.venv\Scripts\python.exe -m alirag.cli serve
