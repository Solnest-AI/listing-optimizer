# Listing Optimizer - Windows setup. Claude Code runs it for you:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -NoPrompt -AutoInstall
# or double-click setup.cmd. Safe to rerun: keeps your .env keys, .venv, config, history, reports.
# Exit codes: 0 ready, 1 stopped (see message), 2 finished but keys or tests need attention.
#
# Built to match the STR Secrets Connections kit (github.com/Solnest-AI/str-secrets-connections):
#   * Python comes from uv, never winget and never the Microsoft Store shim. The Claude Code
#     desktop app is a Store (MSIX) app that silently redirects AppData writes, so uv's Python
#     lives under %USERPROFILE%\.uv\python, the same place the kit puts it.
#   * Keys never touch the chat. They are copied from the kit (scripts\kit_link.py). Anything
#     blank or rejected is pasted by the attendee into the kit's .env (the one place keys live),
#     which this script opens; the next run copies it over. Without a kit, this folder's .env.
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
# configured location first, then the kit's profile location, then uv's default. Leaves
# UV_PYTHON_INSTALL_DIR exactly as it found it.
function Find-UvPython($uv) {
    $orig = $env:UV_PYTHON_INSTALL_DIR
    $homes = @()
    if ($orig) { $homes += $orig }
    $homes += "$env:USERPROFILE\.uv\python"
    $homes += ''   # uv's own default
    $found = $null
    foreach ($h in $homes) {
        $env:UV_PYTHON_INSTALL_DIR = $h
        $env:UV_PYTHON_DOWNLOADS = 'never'
        # --no-project --system: the real interpreter (uv-managed or not), never this folder's
        # .venv. No Select-Object in this pipeline: it ends the process early and corrupts
        # $LASTEXITCODE.
        try { $p = @(& $uv python find --no-project --system $PyVersion 2>$null) } catch { $p = @() }
        $rc = $LASTEXITCODE
        $env:UV_PYTHON_DOWNLOADS = $null
        if ($rc -eq 0 -and $p.Count -gt 0 -and (Test-Path "$($p[0])".Trim())) { $found = "$($p[0])".Trim(); break }
    }
    $env:UV_PYTHON_INSTALL_DIR = $orig
    return $found
}
# Does this venv's python actually run? A .venv copied from another machine or account still
# has python.exe but points at an interpreter that is not there.
function Test-Venv($py) {
    if (-not (Test-Path $py)) { return $false }
    try { & $py -c 'import sys' 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
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
if (-not $uv) {
    Fail ('uv could not be installed. Check your internet connection and run setup again, or install it ' +
          'yourself from https://docs.astral.sh/uv/getting-started/installation/ and rerun.')
}
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
if (-not $PyExe) {
    Fail ("uv could not provide Python $PyVersion. Check your internet connection and run setup again. " +
          "If it keeps failing, open the kit's connectors/system-python-uv.md, section 6.")
}
Ok "Python $PyVersion (uv): $PyExe"

# ---------------------------------------------------------------- 2. Git
Step 2 'Git for Windows (Claude Code uses its Git Bash)'
if (Get-Command git -ErrorAction SilentlyContinue) {
    Ok ((git --version) -replace '^git version ', 'Git ')
} else {
    Warn 'Git is not installed. Claude Code on Windows works better with it.'
    if (Ask '  Install Git now with winget?') { Install-WithWinget 'Git.Git' @() }
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Ok 'Git installed. Restart Claude Code before the next step so it can use Git Bash.'
        $Problems.Add('Git was just installed: quit and reopen Claude Code, then say "Set up the Listing Optimizer" again.')
    } else {
        $Problems.Add('Install Git from https://git-scm.com/download/win, then restart Claude Code.')
    }
}

# ---------------------------------------------------------------- 3. venv + packages
Step 3 'Python environment and packages (first time takes a minute)'
if ((Test-Path '.venv') -and -not (Test-Venv $VenvPy)) {
    Warn '.venv does not run here (copied from another machine or a Mac?). Rebuilding it.'
    Remove-Item -Recurse -Force '.venv'
}
if (-not (Test-Path $VenvPy)) {
    & $uv venv .venv --python $PyExe --quiet
    if ($LASTEXITCODE -ne 0 -or -not (Test-Venv $VenvPy)) { Fail 'Could not create .venv.' }
}
& $uv pip install --python $VenvPy --quiet -r requirements.txt -r requirements-dev.txt
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
$kitOut = @(& $VenvPy scripts\kit_link.py @kitArgs)
$kitRc = $LASTEXITCODE
$kitOut | ForEach-Object { Write-Host $_ }
$KitDir = ''
if ($kitRc -eq 0 -or $kitRc -eq 2) {
    # Only a successful link prints the kit's path; never parse the explanation lines as paths.
    foreach ($line in $kitOut) {
        if ($line -match '^\[kit\] ([A-Za-z]:\\.+)$') {
            $cand = $Matches[1]
            try { if (Test-Path -LiteralPath ([IO.Path]::Combine($cand, 'fan-out-env.sh'))) { $KitDir = $cand; break } } catch { }
        }
    }
}
if ($kitRc -eq 1) { Warn 'No kit found: standalone install. Keys live in this folder''s .env only (github.com/Solnest-AI/str-secrets-connections has the kit).' }
if ($kitRc -eq 3) {
    # A kit exists but was never set up: keys belong in it, not here. Nothing to open yet.
    Warn 'Your connections kit is not set up yet, so there are no keys to copy.'
    $Problems.Add('Open the str-secrets-connections folder in Claude Code, say "Set up my connections", then run this setup again.')
}
# Where a missing or rejected key gets pasted. With a kit linked, the kit's .env is the one
# place keys live and the next run copies it over; a value pasted here would be overwritten.
$EnvToOpen = $EnvFile; $EnvLabel = 'this folder''s .env'
if ($KitDir) { $EnvToOpen = Join-Path $KitDir '.env'; $EnvLabel = "the kit's .env ($EnvToOpen)" }
$NeedEnv = ($kitRc -eq 2)

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
if ($LASTEXITCODE -ne 0 -and $kitRc -ne 3) {
    $Problems.Add("Paste each key marked !! into $EnvLabel (never into the chat), save, then rerun setup or say `"saved`".")
    $NeedEnv = $true
}
if ($NeedEnv -and -not $env:LO_NO_OPEN) {
    # The credential contract: keys go into the file, never into the chat and never through a
    # prompt. Open the file so the attendee pastes what is missing or rejected, and saves.
    Write-Host "  Opening $EnvLabel : paste each key marked !! after its = sign, save, then say `"saved`"."
    # Notepad is a Store app on Windows 11 and can be missing (uninstalled, Windows Sandbox).
    # The editor must never take the run down with it: try Notepad, then the default handler,
    # then just say where the file is.
    $opened = $false
    foreach ($try in @({ Start-Process notepad.exe -ArgumentList "`"$EnvToOpen`"" -ErrorAction Stop },
                       { Start-Process $EnvToOpen -ErrorAction Stop })) {
        try { & $try; $opened = $true; break } catch { }
    }
    if (-not $opened) { Warn "Could not open an editor. Open this file yourself: $EnvToOpen" }
}

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
