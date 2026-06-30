@echo off
REM Full re-parse of every PDF with Docling (layout/tables/reading-order + GPU OCR).
REM Resumable: only re-processes PDFs not yet tagged 'docling' in the index.
REM Single-instance lock + keep-system-awake are handled inside ingest.py.
set RAG_DOCLING=1
set PYTHONUNBUFFERED=1
"%~dp0.venv\Scripts\python.exe" "%~dp0ingest.py" --redocling >> "%~dp0docling_pass.log" 2>&1
