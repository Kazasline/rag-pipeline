<#
.SYNOPSIS
    Set up jcode (https://jcode.sh) on this Windows machine and hand over at the Claude login.

.DESCRIPTION
    jcode is an open-source terminal coding-agent harness (Rust) that drives the AI
    subscriptions you already pay for - Claude, ChatGPT/Codex, Gemini, Copilot, and others.

    This script automates every step that CAN be automated:

      1. Preflight  - Windows / PowerShell 5.1+ / architecture / TLS 1.2 check.
      2. Install    - runs the official installer (irm https://jcode.sh/install.ps1 | iex).
      3. PATH       - refreshes PATH in the CURRENT session so jcode works right away.
      4. Verify     - jcode --version + SHA-256 of the installed binary (compare vs SHA256SUMS).
      5. Credentials- reports which providers already have local credentials.
      6. Login      - HANDS OVER to `jcode login --provider claude`. A browser opens and you
                      enter your Anthropic email/password there. Nothing is typed by the script,
                      and no credential ever passes through it.
      7. Auth test  - jcode auth-test --all-configured
      8. Smoke test - jcode run "say hello"
      9. Optional   - writes .mcp.json so the repo's file_rag MCP tool is available inside jcode.

.PARAMETER Provider
    Provider to log in with. Default: claude. Others: openai, gemini, copilot, azure, ...

.PARAMETER SkipInstall
    Skip the download/install step (jcode already installed; you only want login + checks).

.PARAMETER SkipLogin
    Do everything except the interactive login. Run `jcode login --provider claude` yourself later.

.PARAMETER SkipSmokeTest
    Skip the final `jcode run "say hello"` (it consumes tokens from your subscription).

.PARAMETER Headless
    Use `--no-browser` login: jcode prints an auth URL you open manually, then you paste the
    code/callback back. Use over RDP/SSH or when no local browser is available.

.PARAMETER ConfigureAlacritty
    Ask the official installer to also install the Alacritty terminal (opt-in, off by default).

.PARAMETER ConfigureHotkey
    Ask the official installer to also register the global launch hotkey (opt-in, off by default).

.PARAMETER BuildFromSource
    Force a source build if no prebuilt Windows asset matches. Needs Git, Rust, and
    Visual Studio 2022 Build Tools with the "Desktop development with C++" workload.

.PARAMETER WireRagMcp
    Write .mcp.json at the repo root pointing at this project's rag_mcp.py, so the `file_rag`
    tool shows up inside jcode. jcode reads Claude Code's .mcp.json format natively.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\setup-jcode.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\setup-jcode.ps1 -WireRagMcp -SkipSmokeTest

.NOTES
    Sources: https://github.com/1jehuang/jcode (README, docs/WINDOWS.md).
    Install paths: %LOCALAPPDATA%\jcode\bin\jcode.exe   config/auth: %USERPROFILE%\.jcode
#>
[CmdletBinding()]
param(
    [string] $Provider = "claude",
    [switch] $SkipInstall,
    [switch] $SkipLogin,
    [switch] $SkipSmokeTest,
    [switch] $Headless,
    [switch] $ConfigureAlacritty,
    [switch] $ConfigureHotkey,
    [switch] $BuildFromSource,
    [switch] $WireRagMcp
)

$ErrorActionPreference = "Stop"
$script:StepNo = 0

function Step   ($m) { $script:StepNo++; Write-Host ""; Write-Host ("[{0}] {1}" -f $script:StepNo, $m) -ForegroundColor Cyan }
function Ok     ($m) { Write-Host "    OK    $m" -ForegroundColor Green }
function Info   ($m) { Write-Host "    ..    $m" -ForegroundColor Gray }
function Warn   ($m) { Write-Host "    WARN  $m" -ForegroundColor Yellow }
function Fail   ($m) { Write-Host "    FAIL  $m" -ForegroundColor Red }
function Action ($m) { Write-Host "    YOU   $m" -ForegroundColor Magenta }

function Refresh-Path {
    # The installer adds %LOCALAPPDATA%\jcode\bin to the *user* PATH, but the running process
    # keeps its old copy. Rebuild it from the registry so `jcode` resolves without a new window.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ } ) -join ";"
    $local = Join-Path $env:LOCALAPPDATA "jcode\bin"
    if ((Test-Path $local) -and ($env:Path -notlike "*$local*")) { $env:Path = "$local;$env:Path" }
}

function Get-Jcode {
    Get-Command jcode -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== jcode setup =============================================================" -ForegroundColor White
Write-Host "  Harness: jcode (open source, MIT)   Provider to authenticate: $Provider"
Write-Host "  This script stops at the login screen - that is where YOU enter credentials."
Write-Host "=============================================================================" -ForegroundColor White

# ── 1. Preflight ──────────────────────────────────────────────────────────────
Step "Preflight checks"

$psv = $PSVersionTable.PSVersion
if ($psv.Major -lt 5 -or ($psv.Major -eq 5 -and $psv.Minor -lt 1)) {
    Fail "PowerShell $psv found; the jcode installer needs 5.1 or later."
    Action "Install PowerShell 7 (winget install Microsoft.PowerShell) and re-run this script."
    exit 1
}
Ok "PowerShell $psv"

if (-not $IsWindows -and $psv.Major -ge 6) {
    Fail "This script targets Windows. On macOS/Linux use: curl -fsSL https://jcode.sh/install | bash"
    exit 1
}

$arch = $env:PROCESSOR_ARCHITECTURE
Ok "Architecture $arch (installer picks x64 or ARM64 automatically)"

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Ok "TLS 1.2 enabled for this session"
} catch {
    Warn "Could not force TLS 1.2 - the download may fail on older Windows builds."
}

$existing = Get-Jcode
if ($existing) {
    Info "jcode already on PATH at $($existing.Source)"
} else {
    Refresh-Path
    $existing = Get-Jcode
    if ($existing) { Info "jcode found after PATH refresh: $($existing.Source)" }
}

# ── 2. Install ────────────────────────────────────────────────────────────────
Step "Install jcode"

if ($SkipInstall) {
    Info "-SkipInstall set; leaving the current installation alone."
    if (-not (Get-Jcode)) { Fail "...but jcode is not on PATH. Re-run without -SkipInstall."; exit 1 }
} else {
    if ($existing) {
        Info "Existing install detected. The official installer updates in place; continuing."
    }
    $installerArgs = @{}
    if ($ConfigureAlacritty) { $installerArgs["ConfigureAlacritty"] = $true }
    if ($ConfigureHotkey)    { $installerArgs["ConfigureHotkey"]    = $true }
    if ($BuildFromSource)    { $installerArgs["BuildFromSource"]    = $true }

    Info "Downloading https://jcode.sh/install.ps1"
    Info "The installer verifies the download against the release SHA256SUMS before installing."
    try {
        $installerText = Invoke-RestMethod -Uri "https://jcode.sh/install.ps1" -UseBasicParsing
    } catch {
        Fail "Could not download the installer: $($_.Exception.Message)"
        Action "Check your connection/proxy, then re-run. Manual fallback: irm https://jcode.sh/install.ps1 | iex"
        exit 1
    }

    $installer = [scriptblock]::Create($installerText)
    if ($installerArgs.Count -gt 0) {
        Info ("Running installer with: " + (($installerArgs.Keys | ForEach-Object { "-$_" }) -join " "))
        & $installer @installerArgs
    } else {
        Info "Running installer with defaults (no Alacritty, no global hotkey)"
        & $installer
    }
    Ok "Installer finished"
}

# ── 3. PATH ───────────────────────────────────────────────────────────────────
Step "Make jcode available in this session"
Refresh-Path
$jc = Get-Jcode
if (-not $jc) {
    Fail "jcode is still not on PATH."
    Action "Close this window, open a NEW PowerShell, run 'jcode --version'. If it still fails, check %LOCALAPPDATA%\jcode\bin."
    exit 1
}
Ok "jcode -> $($jc.Source)"

# ── 4. Verify ─────────────────────────────────────────────────────────────────
Step "Verify the installation"
$version = (& jcode --version 2>&1 | Out-String).Trim()
Ok "Version: $version"

try {
    $hash = (Get-FileHash $jc.Source -Algorithm SHA256).Hash
    Info "SHA-256: $hash"
    Info "Compare against SHA256SUMS at https://github.com/1jehuang/jcode/releases/latest"
} catch { Warn "Could not hash the binary: $($_.Exception.Message)" }

try {
    $sig = Get-AuthenticodeSignature $jc.Source
    if ($sig.Status -eq "Valid") {
        Ok "Authenticode signature: Valid ($($sig.SignerCertificate.Subject))"
    } else {
        Info "Authenticode status: $($sig.Status). Unsigned/new-publisher builds can trip SmartScreen."
        Info "Never disable Defender to work around this - verify URL + checksum instead (docs/WINDOWS.md)."
    }
} catch { Info "Could not read the Authenticode signature: $($_.Exception.Message)" }

# ── 5. Existing credentials ───────────────────────────────────────────────────
Step "Look for credentials you already have (avoids an unnecessary login)"

$home_ = $env:USERPROFILE
$credChecks = @(
    @{ Name = "Claude  (jcode)";       Path = "$home_\.jcode\auth.json" },
    @{ Name = "Claude  (Claude Code)"; Path = "$home_\.claude\.credentials.json" },
    @{ Name = "OpenAI  (jcode)";       Path = "$home_\.jcode\openai-auth.json" },
    @{ Name = "OpenAI  (Codex CLI)";   Path = "$home_\.codex\auth.json" },
    @{ Name = "Gemini  (jcode)";       Path = "$home_\.jcode\gemini_oauth.json" },
    @{ Name = "Gemini  (gemini CLI)";  Path = "$home_\.gemini\oauth_creds.json" }
)
$haveClaude = $false
foreach ($c in $credChecks) {
    if (Test-Path $c.Path) {
        Ok "$($c.Name) -> $($c.Path)"
        if ($c.Name -like "Claude*") { $haveClaude = $true }
    } else {
        Info "$($c.Name) -> none"
    }
}
foreach ($v in @("ANTHROPIC_API_KEY","OPENAI_API_KEY","OPENROUTER_API_KEY")) {
    if ([Environment]::GetEnvironmentVariable($v)) { Ok "env $v is set" }
}

# ── 6. Login (interactive - this is your step) ─────────────────────────────────
Step "Log in to $Provider"

if ($SkipLogin) {
    Info "-SkipLogin set."
    Action "Run this yourself when ready:  jcode login --provider $Provider"
} elseif ($haveClaude -and $Provider -eq "claude" -and (Test-Path "$home_\.jcode\auth.json")) {
    Ok "jcode already holds Claude credentials at $home_\.jcode\auth.json - skipping login."
    Info "Force a fresh login (or switch account) with:  jcode login --provider claude"
} else {
    Write-Host ""
    Action "HANDOVER: the next command opens your browser at Anthropic's sign-in page."
    Action "Sign in with the account that carries your monthly Claude subscription, then approve access."
    Action "Your password is typed into Anthropic's page only - this script never sees or stores it."
    if ($Headless) { Action "Headless mode: jcode prints a URL/code instead. Open it on any device and paste the result back." }
    Write-Host ""
    Read-Host "    Press Enter to start the login (Ctrl+C to abort)" | Out-Null

    $loginArgs = @("login", "--provider", $Provider)
    if ($Headless) { $loginArgs += "--no-browser" }
    & jcode @loginArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "Login exited with code $LASTEXITCODE."
        Action "Retry manually:  jcode login --provider $Provider   (add --no-browser if no browser opens)"
    } else {
        Ok "Login flow completed"
    }
}

# ── 7. Auth test ──────────────────────────────────────────────────────────────
Step "Test the configured providers"
& jcode auth-test --all-configured
if ($LASTEXITCODE -eq 0) { Ok "auth-test passed" } else { Warn "auth-test reported problems (exit $LASTEXITCODE) - see output above." }

# ── 8. Smoke test ─────────────────────────────────────────────────────────────
Step "Smoke test"
if ($SkipSmokeTest) {
    Info "-SkipSmokeTest set. Try it later with:  jcode run ""say hello"""
} else {
    Info "Running: jcode run ""say hello""  (uses a few tokens from your subscription)"
    & jcode run "say hello"
    if ($LASTEXITCODE -eq 0) { Ok "jcode answered - the harness is live" } else { Warn "Smoke test exit code $LASTEXITCODE" }
}

# ── 9. Optional: wire this repo's RAG MCP server into jcode ───────────────────
if ($WireRagMcp) {
    Step "Wire the file_rag MCP tool into jcode for this repo"
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $py       = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $server   = Join-Path $repoRoot "rag_mcp.py"
    $mcpPath  = Join-Path $repoRoot ".mcp.json"

    if (-not (Test-Path $py))     { Warn "No venv python at $py - create it first (python -m venv .venv)." }
    if (-not (Test-Path $server)) { Warn "rag_mcp.py not found at $server." }

    if (Test-Path $mcpPath) {
        Warn ".mcp.json already exists - leaving it untouched. Delete it first if you want a fresh one."
    } else {
        $mcp = [ordered]@{ mcpServers = [ordered]@{ "file-rag" = [ordered]@{
            command = $py
            args    = @($server)
            env     = @{}
        } } }
        ($mcp | ConvertTo-Json -Depth 6) | Set-Content -Path $mcpPath -Encoding UTF8
        Ok "Wrote $mcpPath"
        Info 'jcode reads Claude Code''s .mcp.json natively, so the file_rag tool appears in jcode sessions here.'
        Info "It holds absolute local paths - .gitignore already excludes it."
    }
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Done ====================================================================" -ForegroundColor White
Write-Host "  Binary : $($jc.Source)"
Write-Host "  Config : $home_\.jcode\config.toml"
Write-Host "  Auth   : $home_\.jcode\auth.json  (Claude)"
Write-Host ""
Write-Host "  Next:"
Write-Host "    cd `"$(Split-Path -Parent $PSScriptRoot)`""
Write-Host "    jcode                       # launch the TUI"
Write-Host "    /model                      # pick the Claude model inside the TUI"
Write-Host "    jcode run `"...`"             # one-shot, non-interactive"
Write-Host "    jcode --resume <name>       # resume a past session"
Write-Host "    jcode login --provider openai   # add ChatGPT/Codex as a second provider"
Write-Host "    /account                    # switch between accounts inside the TUI"
Write-Host "=============================================================================" -ForegroundColor White
