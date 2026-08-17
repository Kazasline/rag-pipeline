@echo off
setlocal enabledelayedexpansion
title ALI RAG V3 - GO  (pull model -> ingest pilot -> first real answer)
color 0A

rem ===================================================================
rem  Takes the system from "installed" to "answering a real question".
rem
rem   1 update repo + install machine-specific config
rem   2 pull qwen3.8:27b (18 GB) if missing
rem   3 re-inspect  (proves the model is really there)
rem   4 inventory   (incremental - unchanged files skip)
rem   5 PILOT ingest of a few hundred files (minutes, not hours)
rem   6 status      (proves index + LLM are live)
rem   7 real queries: retrieval-only, then FAST, then DEEP
rem   8 safety verify + one report to send back
rem
rem  READ-ONLY on your documents throughout. Nothing on E:\ is deleted,
rem  moved, renamed or edited. Only E:\ALI_RAG\ is written.
rem ===================================================================

set "TARGET=C:\rag-pipeline"
set "PY=%TARGET%\.venv\Scripts\python.exe"
set "MODEL=qwen3.8:27b"
set "PILOT=300"
set "REPORT=%USERPROFILE%\Desktop\ALIRAG_RUN_REPORT.txt"

if not exist "%PY%" (
  echo  [X] %PY% tiada. Jalankan SETUP_V3.bat dahulu.
  goto :end
)
cd /d "%TARGET%"

echo.
echo  [1/8] kemas kini repo + pasang config
git pull --quiet origin claude/ali-agentic-rag-v3-m9gu50
if not exist "E:\ALI_RAG\01_CONFIG" mkdir "E:\ALI_RAG\01_CONFIG"
copy /y "%TARGET%\v3\config.kazasline.yaml" "E:\ALI_RAG\01_CONFIG\config.yaml" >nul
echo       [OK] config dipasang

echo.
echo  [2/8] semak model %MODEL%
ollama list | findstr /i "qwen3.8" >nul 2>&1
if errorlevel 1 (
  echo       [..] belum ada - muat turun 18 GB, ini ambil masa
  echo       ^(biarkan tetingkap ini terbuka^)
  ollama pull %MODEL%
  if errorlevel 1 (
    echo       [X] pull gagal - semak internet / ruang cakera C:
    goto :report
  )
) else (
  echo       [OK] model sudah ada
)

cd /d "%TARGET%\v3"

echo.
echo  [3/8] periksa mesin semula
> "%REPORT%" echo ============ ALI RAG V3 - RUN REPORT ============
>> "%REPORT%" echo Tarikh: %DATE% %TIME%
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- INSPECT ----------------
"%PY%" -m alirag.cli inspect >> "%REPORT%" 2>&1
echo       [OK] siap

echo.
echo  [4/8] inventory (incremental)
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- INVENTORY ----------------
"%PY%" -m alirag.cli inventory --max-files 20000 >> "%REPORT%" 2>&1
echo       [OK] siap

echo.
echo  [5/8] PILOT ingest - %PILOT% fail (beberapa minit)
echo       parse + chunk + embed + graph. Progres di bawah:
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- INGEST (pilot %PILOT%) ----------------
rem live progress on screen AND captured to the report (cmd has no tee)
powershell -NoProfile -Command "& '%PY%' -m alirag.cli ingest --limit %PILOT% 2>&1 | Tee-Object -FilePath '%TEMP%\alirag_ingest.log'"
if exist "%TEMP%\alirag_ingest.log" type "%TEMP%\alirag_ingest.log" >> "%REPORT%"
echo       [OK] ingest pilot selesai

echo.
echo  [6/8] status sistem
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- STATUS ----------------
"%PY%" -m alirag.cli status
"%PY%" -m alirag.cli status >> "%REPORT%" 2>&1

echo.
echo  [7/8] soalan sebenar
echo       (a) retrieval sahaja - buktikan carian jumpa sumber betul
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- QUERY A: retrieval only ----------------
"%PY%" -m alirag.cli query "cepat: senarai dokumen tender" --no-llm >> "%REPORT%" 2>&1
echo       (b) FAST - jawapan pendek + petikan sumber
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- QUERY B: FAST (with LLM) ----------------
"%PY%" -m alirag.cli query "cepat: dokumen apa yang ada dalam index?" >> "%REPORT%" 2>&1
echo       (c) DEEP - guna %MODEL%
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- QUERY C: DEEP (with LLM) ----------------
"%PY%" -m alirag.cli query "deep: apakah kandungan utama dokumen yang diindeks?" >> "%REPORT%" 2>&1
echo       [OK] siap

echo.
echo  [8/8] sahkan keselamatan + kutip metrik
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- SAFETY VERIFY ----------------
"%PY%" -m alirag.cli safety verify >> "%REPORT%" 2>&1
>> "%REPORT%" echo.
>> "%REPORT%" echo ---------------- METRICS (latensi sebenar) ----------------
"%PY%" -m alirag.cli metrics >> "%REPORT%" 2>&1
echo       [OK] siap

:report
>> "%REPORT%" echo.
>> "%REPORT%" echo ============ TAMAT ============
echo.
echo  ===============================================================
echo   SIAP. Laporan: %REPORT%
echo   Copy semua, paste kepada Claude.
echo  ===============================================================
echo.
echo   Ini PILOT (%PILOT% fail sahaja) supaya anda nampak ia berfungsi
echo   dengan cepat. Untuk index penuh kemudian:
echo       ..\.venv\Scripts\python -m alirag.cli inventory
echo       ..\.venv\Scripts\python -m alirag.cli ingest
echo.
start notepad "%REPORT%"

:end
pause
endlocal
