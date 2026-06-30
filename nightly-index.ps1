# nightly-index.ps1 - nightly incremental Docling re-index of the whole P:\ drive.
# Scheduled Task "RAG-Nightly-Index" (~02:00). Picks up NEW and CHANGED documents (unchanged
# files skip via path+mtime+size), Docling-parsing PDFs for layout/tables/OCR.
#
# UNATTENDED HANG PROTECTION: a pathological scanned PDF can hang Docling with no timeout. This
# wrapper watches the ingest log; if it stops growing for $STALL_MIN, it reads the in-progress
# file from refresh_current.txt, adds it to docling_poison.txt (so it is skipped next time),
# kills ONLY the ingest (matched by 'ingest.py' on the command line - never the file_rag MCP
# server), and relaunches. Capped by $MAX_STALLS and a $HARD_CAP_MIN total budget.

$ErrorActionPreference = "SilentlyContinue"
$PIPE   = "P:\RAG Database\pipeline"
$PY     = "$PIPE\.venv\Scripts\python.exe"
$LOG    = "$PIPE\refresh.log"
$NLOG   = "$PIPE\nightly.log"
$MARKER = "C:\RAGData\index\refresh_current.txt"
$POISON = "C:\RAGData\index\docling_poison.txt"
$NOTIFY = @("+60125020189")           # Ali - always notified when the RAG update finishes
$STALL_MIN    = 20                     # no log growth this long = a hung file
$MAX_STALLS   = 8                      # give up after this many poison-and-retry cycles
$HARD_CAP_MIN = 240                    # absolute time budget for the whole run

function nlog($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Add-Content $NLOG -Encoding UTF8 }
function ingest_procs { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'ingest\.py' } }
function notify($t) { foreach ($n in $NOTIFY) { & openclaw message send --channel whatsapp --target $n --message $t 2>&1 | Out-Null } }

nlog "=== nightly refresh start ==="
$startAll = Get-Date
$stalls = 0
$env:PYTHONUNBUFFERED = "1"                   # CRITICAL: flush progress to the log in real time,
                                              # otherwise buffered output breaks stall detection
"" | Set-Content $LOG -Encoding UTF8          # fresh log so growth-tracking is clean

while ($true) {
    if (-not (ingest_procs)) {
        Start-Process -FilePath $PY -ArgumentList @("`"$PIPE\ingest.py`"", "--refresh") `
            -WindowStyle Hidden -RedirectStandardOutput $LOG -RedirectStandardError "$LOG.err"
        nlog "launched ingest --refresh"
        Start-Sleep -Seconds 25
    }
    $lastSize = (Get-Item $LOG).Length
    $lastGrew = Get-Date
    $stalled  = $false
    while (ingest_procs) {
        Start-Sleep -Seconds 60
        if ((Get-Date) -gt $startAll.AddMinutes($HARD_CAP_MIN)) {
            ingest_procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
            nlog "HARD CAP ${HARD_CAP_MIN}min -> stopped"
            notify("RAG nightly index hit the ${HARD_CAP_MIN}-min limit and was stopped; it will continue tomorrow.")
            $stalls = -1            # signal: stop, already notified
            break
        }
        $sz = (Get-Item $LOG).Length
        if ($sz -gt $lastSize) { $lastSize = $sz; $lastGrew = Get-Date }
        elseif ((Get-Date) -gt $lastGrew.AddMinutes($STALL_MIN)) {
            $cur = (Get-Content $MARKER -Raw -EA SilentlyContinue)
            if ($cur) { $cur.Trim() | Add-Content $POISON -Encoding UTF8; nlog "STALL -> poisoned: $($cur.Trim())" }
            else { nlog "STALL but marker empty" }
            ingest_procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
            $stalls++
            $stalled = $true
            break
        }
    }
    if ($stalled -and $stalls -lt $MAX_STALLS) { Start-Sleep 5; continue }   # relaunch (skips poison)
    break
}

# Summary line from the ingest log (last progress line)
$summary = (Get-Content $LOG -Tail 10 -EA SilentlyContinue | Where-Object { $_ -match 'ok=' } | Select-Object -Last 1)
if (-not $summary) { $summary = "(no progress line)" }
nlog "=== nightly refresh end (stalls=$stalls) :: $summary ==="

# ALWAYS notify Ali when the run finishes - success, no-change, or problem (one message only).
$indexedSomething = ($summary -match 'ok=([1-9]\d*)')
if ($stalls -eq -1) {
    # hard cap was hit and already notified above - don't double-message
} elseif ($summary -eq "(no progress line)") {
    notify("[WARN] RAG update finished but produced NO progress - possible problem (check Ollama / P: drive / Python). Log: $NLOG")
} elseif ($stalls -gt 0) {
    notify("[WARN] RAG update DONE, but skipped $stalls file(s) that hung Docling (listed in docling_poison.txt). $summary")
} elseif ($indexedSomething) {
    notify("[OK] RAG update DONE - new/updated documents added. $summary")
} else {
    notify("[OK] RAG update DONE - no new documents today. $summary")
}
exit 0
