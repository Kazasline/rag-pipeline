@echo off
rem Incremental ingestion of CLASSIFIED/UPDATED files. Safe to re-run any time;
rem unchanged files are skipped by content hash. Add flags as needed:
rem   --docling   structured GPU parsing for PDFs
rem   --ocr       OCR scanned pages (needs Tesseract + msa language data)
rem   --render    pre-render PDF page images for visual/multimodal use
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
..\.venv\Scripts\python.exe -m alirag.cli ingest %*
pause
