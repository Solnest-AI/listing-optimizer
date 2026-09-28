# Listing Optimizer - Windows setup. Claude Code runs it for you:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -NoPrompt -AutoInstall
# or double-click setup.cmd. Safe to rerun: keeps your .env keys, .venv, config, history, reports.
# Exit codes: 0 ready, 1 stopped (see message), 2 finished but keys or tests need attention.
#
# Built to match the STR Secrets Connections kit (github.com/Solnest-AI/str-secrets-connections):
#   * Python comes from uv, never winget and never the Microsoft Store shim. The Claude Code
#     desktop app is a Store (MSIX) app that silently redirects AppData writes, so uv's Python
#     lives under %USERPROFILE%\.uv\python, the same place the kit puts it.
#   * Keys never touch the chat. They are copied from the kit (scripts\kit_link.py); anything
#     still blank is pasted by the attendee into .env, which this script opens in Notepad.
# Keep this file ASCII-only: Windows PowerShell 5.1 misreads UTF-8 without a BOM.

param(
    [switch]$SkipTests,      # skip the ~30s test suite
    [switch]$NoPrompt,       # never ask questions (Claude Code / scripted runs)
    [switch]$AutoInstall,    # install missing uv, Python and Git without asking
    [string]$Kit = ''        # the STR Secrets Connections folder, if it is somewhere unusual
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$EnvFile = Join-Path $Root '.env'
$Problems = New-Object System.Collections.Generic.List[string]
$PyVersion = '3.13'

function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !!  $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ""; Write-Host "  SETUP STOPPED: $text" -ForegroundColor Red
    Write-Host ""; exit 1
}
function Ask($question) {
    if ($AutoInstall) { return $true }
    if ($NoPrompt) { return $false }
    return (Read-Host "$question [Y/n]") -notmatch '^[nN]'
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Get-Winget {
    # winget ships with Windows 11 but a damaged PATH can hide it; its home is fixed.
    $cmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fixed = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
    if (Test-Path $fixed) { return $fixed }
    return $null
}
function Install-WithWinget($id, $extra) {
    $winget = Get-Winget
    if (-not $winget) {
        Warn 'winget is not available on this PC (it ships with Windows 10 1709+ / Windows 11).'
        return
    }
    Write-Host "  Installing $id with winget (a Windows prompt may ask you to approve it)..."
    # --source winget: only the community repo. Without it winget also queries the Microsoft
    # Store and aborts with 0x8a15003b when the Store is unreachable or disabled (seen in a
    # clean Windows Sandbox), even though the package is in the community repo.
    $common = @('install', '--id', $id, '-e', '--source', 'winget', '--disable-interactivity',
                '--accept-package-agreements', '--accept-source-agreements')
    & $winget @common @extra
    if ($LASTEXITCODE -ne 0 -and $extra.Count -gt 0) {
        & $winget @common
    }
    Refresh-Path
}

# uv: on PATH, or where its installer and winget put it (the app's PATH is fixed at launch,
# so a uv installed earlier today may be on disk but not on PATH yet).
function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @("$HOME\.local\bin\uv.exe", "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
# The Python 3.13 uv can run right now, without downloading anything. Tries the attendee's
# configured location first, then the kit's profile location.
function Find-UvPython($uv) {
    $homes = @()
    if ($env:UV_PYTHON_INSTALL_DIR) { $homes += $env:UV_PYTHON_INSTALL_DIR }
    $homes += "$env:USERPROFILE\.uv\python"
    $homes += ''   # uv's own default
    foreach ($h in $homes) {
        $env:UV_PYTHON_INSTALL_DIR = $h
        $env:UV_PYTHON_DOWNLOADS = 'never'
        # No Select-Object in this pipeline: it ends the process early and corrupts $LASTEXITCODE.
        try { $p = @(& $uv python find $PyVersion 2>$null) } catch { $p = @() }
        $rc = $LASTEXITCODE
        $env:UV_PYTHON_DOWNLOADS = $null
        if ($rc -eq 0 -and $p.Count -gt 0 -and (Test-Path "$($p[0])".Trim())) { return "$($p[0])".Trim() }
    }
    $env:UV_PYTHON_INSTALL_DIR = $null
    return $null
}
# A real system Python 3.10+, as a fallback when uv cannot be installed. Runs each candidate:
# the Microsoft Store stub prints no version, so it is rejected here.
function Find-SystemPython {
    $candidates = @(@('py', '-3'), @('python'), @('python3'))
    foreach ($dir in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles")) {
        if (-not (Test-Path $dir)) { continue }
        Get-ChildItem $dir -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path $exe) { $candidates += , @($exe) }
            }
    }
    foreach ($c in $candidates) {
        $exe = $c[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $args_ = @($c | Select-Object -Skip 1) + @('-c', 'import sys; print(sys.version_info[0], sys.version_info[1])')
        try { $ver = @(& $exe @args_ 2>$null) } catch { continue }
        if ($LASTEXITCODE -ne 0 -or $ver.Count -eq 0) { continue }
        $parts = "$($ver[0])".Trim().Split(' ')
        if ($parts.Count -eq 2 -and [int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) { return , $c }
    }
    return $null
}

Write-Host ""
Write-Host "  Listing Optimizer setup" -ForegroundColor White
Write-Host "  Folder: $Root"

# ---------------------------------------------------------------- 1. uv + Python
Step 1 "Python $PyVersion through uv (never the Microsoft Store)"
$uv = Find-Uv
if (-not $uv -and (Ask '  Install uv now (Astral installer, no admin prompt)?')) {
    Write-Host '  Installing uv into your user folder...'
    try {
        $env:UV_NO_MODIFY_PATH = $null
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
    } catch { Warn "uv installer failed: $($_.Exception.Message)" }
    $uv = Find-Uv
}
$PyExe = $null; $PyCmd = $null
if ($uv) {
    Ok ("uv " + ((& $uv --version 2>$null) -replace '^uv ', ''))
    $PyExe = Find-UvPython $uv
    if (-not $PyExe -and (Ask "  Install Python $PyVersion through uv now?")) {
        $pyHome = if ($env:UV_PYTHON_INSTALL_DIR) { $env:UV_PYTHON_INSTALL_DIR } else { "$env:USERPROFILE\.uv\python" }
        Write-Host "  Installing Python $PyVersion under $pyHome ..."
        $env:UV_PYTHON_INSTALL_DIR = $pyHome
        & $uv python install $PyVersion | Out-Null
        if (-not $env:LO_NO_PERSIST) { setx UV_PYTHON_INSTALL_DIR $pyHome | Out-Null }   # same pin as the kit
        $PyExe = Find-UvPython $uv
    }
    if ($PyExe) { Ok "Python $PyVersion (uv): $PyExe" }
    else { Warn "uv could not provide Python $PyVersion." }
} else {
    Warn 'uv is not installed and could not be installed.'
}
if (-not $PyExe) {
    $sys = Find-SystemPython
    if ($sys) {
        $PyCmd = $sys
        Ok ("falling back to system Python " + (& $sys[0] @($sys | Select-Object -Skip 1) -c 'import sys; print(sys.version.split()[0])'))
    } else {
        Fail ("No Python. Check your internet connection and run setup again (it installs uv and Python " +
              "$PyVersion itself), or install Python from https://www.python.org/downloads/ and rerun.")
    }
}

# ---------------------------------------------------------------- 2. Git
Step 2 'Git for Windows (Claude Code uses its Git Bash)'
if (Get-Command git -ErrorAction SilentlyContinue) {
    Ok ((git --version) -replace '^git version ', 'Git ')
} else {
    Warn 'Git is not installed. Claude Code on Windows works better with it.'
    if (Ask '  Install Git now with winget?') { Install-WithWinget 'Git.Git' @() }
    if (Get-Command git -ErrorAction SilentlyContinue) { Ok 'Git installed (restart Claude Code so it can see it)' }
    else { $Problems.Add('Install Git from https://git-scm.com/download/win, then restart Claude Code.') }
}

# ---------------------------------------------------------------- 3. venv + packages
Step 3 'Python environment and packages (first time takes a minute)'
if ((Test-Path '.venv') -and -not (Test-Path $VenvPy)) {
    Warn '.venv is not a Windows environment (copied from a Mac?). Rebuilding it.'
    Remove-Item -Recurse -Force '.venv'
}
if (-not (Test-Path $VenvPy)) {
    if ($uv -and $PyExe) { & $uv venv .venv --python $PyExe --quiet }
    else { & $PyCmd[0] @($PyCmd | Select-Object -Skip 1) -m venv .venv }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Fail 'Could not create .venv.' }
}
if ($uv) { & $uv pip install --python $VenvPy --quiet -r requirements.txt -r requirements-dev.txt }
else { & $VenvPy -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt }
if ($LASTEXITCODE -ne 0) { Fail 'Package install failed. Check your internet connection and run setup again.' }
Ok "Packages installed in .venv (Python $(& $VenvPy -c 'import sys; print(sys.version.split()[0])'))"

# ---------------------------------------------------------------- 4. keys
Step 4 'API keys: copied from your STR Secrets Connections kit'
if (-not (Test-Path $EnvFile)) {
    Copy-Item '.env.example' $EnvFile
    Ok 'Created .env from .env.example'
} else {
    Ok 'Using your existing .env (nothing overwritten)'
}
$kitArgs = @()
if ($Kit) { $kitArgs = @('--kit', $Kit) }
& $VenvPy scripts\kit_link.py @kitArgs
$kitRc = $LASTEXITCODE
if ($kitRc -eq 1) { Warn 'No kit found. Set it up first (github.com/Solnest-AI/str-secrets-connections), or paste keys into .env by hand.' }
if ($kitRc -ne 0 -and -not $env:LO_NO_OPEN) {
    # The credential contract: keys go into the file, never into the chat and never through
    # a prompt. Open the file so the attendee pastes what is missing and saves.
    Write-Host '  Opening .env in Notepad: paste each missing key after its = sign, save, then say "saved".'
    Start-Process notepad.exe -ArgumentList "`"$EnvFile`""
}

# ---------------------------------------------------------------- 5. tests
Step 5 'Self-test'
if ($SkipTests) {
    Warn 'Skipped (-SkipTests)'
} else {
    $out = & $VenvPy -m pytest -q -p no:cacheprovider 2>&1
    $last = ($out | Select-Object -Last 1) -replace '=', ''
    if ($LASTEXITCODE -eq 0) { Ok $last.Trim() }
    else {
        $out | Select-Object -Last 25 | ForEach-Object { Write-Host "    $_" }
        $Problems.Add('Some self-tests failed (output above). Send a screenshot to the Solnest team.')
    }
}

# ---------------------------------------------------------------- 6. key check
Step 6 'Checking your keys (free, read-only)'
& $VenvPy scripts\check_keys.py
if ($LASTEXITCODE -ne 0) { $Problems.Add('Paste the keys marked !! into .env (never into the chat), save, then rerun setup or say "saved".') }

# ---------------------------------------------------------------- done
Write-Host ""
if ($Problems.Count -eq 0) {
    Write-Host '  ALL SET.' -ForegroundColor Green
} else {
    Write-Host '  ALMOST THERE. Fix these, then run setup again:' -ForegroundColor Yellow
    foreach ($p in $Problems) { Write-Host "   - $p" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host '  Next: in Claude Code, in this folder, say:'
Write-Host '    "Optimize my <listing name> for <season>."'
Write-Host "  Reports land on your Desktop in the 'Listing Optimizer' folder."
Write-Host ""
if ($Problems.Count -gt 0) { exit 2 }
exit 0
