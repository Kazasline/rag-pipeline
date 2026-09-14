@echo off
setlocal enabledelayedexpansion
title ALI RAG V3 - Setup + Phase 0 + Phase 1 (READ-ONLY)
color 0B

rem ===================================================================
rem  ALI RAG V3 — one-click bootstrap.
rem
rem  Does: clone/locate repo -> venv -> install -> PHASE 0 (inspect
rem        machine) -> PHASE 1 (read-only inventory of E:\) -> safety
rem        verify -> writes ONE report file to paste back to Claude.
rem
rem  SAFETY: every step here is READ-ONLY on your source files.
rem  Nothing is deleted, moved, renamed or modified on E:\.
rem  Ingestion is deliberately NOT run — you review the inventory first.
rem ===================================================================

set "REPO_URL=https://github.com/Kazasline/rag-pipeline.git"
set "BRANCH=claude/ali-agentic-rag-v3-m9gu50"
set "TARGET=C:\rag-pipeline"
set "REPORT=%USERPROFILE%\Desktop\ALIRAG_REPORT_FOR_CLAUDE.txt"

echo.
echo  ===============================================================
echo   ALI RAG V3 - SETUP  (read-only, nothing on E:\ is modified)
echo  ===============================================================
echo.

rem ---------------------------------------------------------------- 0. tools
where git >nul 2>&1
if errorlevel 1 (
  echo  [X] GIT TIDAK DIJUMPAI.
  echo      Pasang dari https://git-scm.com/download/win , buka semula CMD, ulang.
  goto :fail
)
echo  [OK] git dijumpai

where python >nul 2>&1
if errorlevel 1 (
  echo  [X] PYTHON TIDAK DIJUMPAI.
  echo      Pasang Python 3.11+ dari https://www.python.org/downloads/
  echo      PENTING: tick "Add python.exe to PATH" semasa install.
  goto :fail
)
echo  [OK] python dijumpai
python --version

rem ---------------------------------------------------------------- 1. locate/clone
if exist "%TARGET%\.git" (
  echo  [OK] repo sedia ada di %TARGET%
  cd /d "%TARGET%"
  git fetch origin 2>nul
) else (
  echo.
  echo  [..] repo tiada - clone ke %TARGET%
  echo       ^(jika diminta login: username = Kazasline, password = Personal Access Token^)
  echo.
  git clone "%REPO_URL%" "%TARGET%"
  if errorlevel 1 (
    echo  [X] clone gagal. Semak sambungan internet / token GitHub.
    goto :fail
  )
  cd /d "%TARGET%"
)

echo  [..] checkout branch V3
git checkout "%BRANCH%" 2>nul
if errorlevel 1 (
  git checkout -b "%BRANCH%" "origin/%BRANCH%"
)
git pull origin "%BRANCH%" 2>nul
for /f "tokens=*" %%i in ('git rev-parse --short HEAD') do set "COMMIT=%%i"
echo  [OK] pada commit !COMMIT!

rem ---------------------------------------------------------------- 2. venv
if not exist "%TARGET%\.venv\Scripts\python.exe" (
  echo  [..] cipta virtual environment
  python -m venv "%TARGET%\.venv"
  if errorlevel 1 (
    echo  [X] gagal cipta venv.
    goto :fail
  )
)
set "PY=%TARGET%\.venv\Scripts\python.exe"
echo  [OK] venv sedia

rem ---------------------------------------------------------------- 3. deps
echo.
echo  [..] pasang kebergantungan ^(beberapa minit^)...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r "%TARGET%\v3\requirements.txt"
if errorlevel 1 (
  echo  [!] sebahagian pakej pilihan gagal - pasang teras sahaja supaya
  echo      Phase 0/1 tetap boleh jalan ^(parser boleh ditambah kemudian^)
  "%PY%" -m pip install --quiet numpy PyYAML
)
echo  [OK] kebergantungan siap

rem ---------------------------------------------------------------- 4. sanity
echo.
echo  [..] semak pakej boleh diimport
cd /d "%TARGET%\v3"
"%PY%" -c "import alirag, alirag.config; print('  [OK] alirag', alirag.__version__)"
if errorlevel 1 (
  echo  [X] pakej alirag gagal diimport.
  goto :fail
)

rem ---------------------------------------------------------------- 5. E: check
if not exist "E:\" (
  echo.
  echo  [!] AMARAN: drive E:\ tidak dijumpai pada mesin ini.
  echo      Phase 1 ^(inventory^) akan dilangkau. Phase 0 tetap jalan.
  set "SKIP_INV=1"
) else (
  echo  [OK] drive E:\ dijumpai
  set "SKIP_INV="
)

rem ---------------------------------------------------------------- 6. report header
> "%REPORT%" echo ================= ALI RAG V3 - LAPORAN UNTUK CLAUDE =================
>> "%REPORT%" echo Tarikh: %DATE% %TIME%
>> "%REPORT%" echo Commit: !COMMIT!
>> "%REPORT%" echo.

rem ---------------------------------------------------------------- 7. PHASE 0
echo.
echo  ===============================================================
echo   PHASE 0 - PERIKSA MESIN  ^(read-only, tiada apa dipasang^)
echo  ===============================================================
>> "%REPORT%" echo ------------------- PHASE 0: MACHINE INSPECT -------------------
"%PY%" -m alirag.cli inspect >> "%REPORT%" 2>&1
if errorlevel 1 (
  echo  [!] Phase 0 melaporkan ralat - butiran ada dalam laporan
) else (
  echo  [OK] Phase 0 selesai
)

rem ---------------------------------------------------------------- 8. PHASE 1
if defined SKIP_INV goto :skipinv

echo.
echo  ===============================================================
echo   PHASE 1 - INVENTORY E:\  ^(READ-ONLY - tiada fail diubah^)
echo  ===============================================================
echo  [..] ambil snapshot keselamatan dahulu...
>> "%REPORT%" echo.
>> "%REPORT%" echo ------------------- SAFETY SNAPSHOT ----------------------------
"%PY%" -m alirag.cli safety snapshot >> "%REPORT%" 2>&1

echo  [..] imbas E:\  ^(pilot: 20000 fail pertama - boleh ambil masa^)
>> "%REPORT%" echo.
>> "%REPORT%" echo ------------------- PHASE 1: INVENTORY -------------------------
"%PY%" -m alirag.cli inventory --max-files 20000 >> "%REPORT%" 2>&1
if errorlevel 1 (
  echo  [!] inventory melaporkan ralat - butiran dalam laporan
) else (
  echo  [OK] inventory selesai
)

echo  [..] sahkan tiada fail asal terjejas...
>> "%REPORT%" echo.
>> "%REPORT%" echo ------------------- SAFETY VERIFY ^(mesti pass:true^) -----------
"%PY%" -m alirag.cli safety verify >> "%REPORT%" 2>&1
echo  [OK] safety verify selesai

:skipinv

rem ---------------------------------------------------------------- 9. done
>> "%REPORT%" echo.
>> "%REPORT%" echo ================= TAMAT LAPORAN =================

echo.
echo  ===============================================================
echo   SIAP.
echo  ===============================================================
echo.
echo   Laporan disimpan di:
echo   %REPORT%
echo.
echo   LANGKAH SETERUSNYA: buka fail tu, copy semua, paste kepada Claude.
echo   Claude akan isikan config yang betul dan sambung ke fasa berikutnya.
echo.
echo   PERINGATAN: tiada fail pada E:\ dipadam, dialih atau diubah.
echo   Ingestion SENGAJA belum dijalankan - anda semak inventory dahulu.
echo.
start notepad "%REPORT%"
goto :end

:fail
echo.
echo  ===============================================================
echo   BERHENTI - baiki isu di atas, kemudian jalankan semula skrip ni.
echo  ===============================================================
echo.

:end
pause
endlocal
