# Listing Optimizer - Windows setup. Double-click setup.cmd, or run:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# Claude Code runs it as:
#   powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -NoPrompt -AutoInstall
# Safe to rerun: keeps your existing .env keys, .venv, config, history and reports.
# Exit codes: 0 ready, 1 stopped (see message), 2 finished but keys/tests need attention.
# Keep this file ASCII-only: Windows PowerShell 5.1 misreads UTF-8 without a BOM.

param(
    [switch]$SkipTests,      # skip the ~30s test suite
    [switch]$NoPrompt,       # never ask questions (Claude Code / scripted runs)
    [switch]$AutoInstall     # install missing Python/Git with winget without asking
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$EnvFile = Join-Path $Root '.env'
$Problems = New-Object System.Collections.Generic.List[string]

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
        # Some manifests have no per-user variant; retry without the scope switch.
        & $winget @common
    }
    Refresh-Path
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Returns the command (as an array) for a working Python 3.10+, or $null.
# Skips the Microsoft Store "python" alias, which exists on every PC but is not Python.
function Find-Python {
    $candidates = @(@('py', '-3'), @('python'), @('python3'))
    # A python.org install that has not reached this process's PATH yet (fresh winget
    # install, or 'Add to PATH' left unticked). Newest version first.
    $dirs = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles", "${env:ProgramFiles(x86)}")
    foreach ($dir in $dirs) {
        if (-not ($dir -and (Test-Path $dir))) { continue }
        Get-ChildItem $dir -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path $exe) { $candidates += , @($exe) }
            }
    }
    foreach ($c in $candidates) {
        $exe = $c[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        # Do not skip WindowsApps by path: the python.org install manager puts a real py.exe
        # there too. The Store stub prints no version, so running it is the reliable test.
        # No quotes inside the -c code: PowerShell 5.1 strips embedded quotes from native args.
        # No Select-Object -First: it kills the process and corrupts $LASTEXITCODE.
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

# ---------------------------------------------------------------- 1. Python
Step 1 'Python 3.10 or newer'
$py = Find-Python
if (-not $py) {
    Warn 'Python 3.10+ was not found.'
    if (Ask '  Install Python 3.12 now with winget?') {
        Install-WithWinget 'Python.Python.3.12' @('--scope', 'user')
        $py = Find-Python
    }
    if (-not $py) {
        Fail ("Install Python from https://www.python.org/downloads/ (tick 'Add python.exe to PATH' " +
              "on the first screen), then run setup again.")
    }
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
Ok ("Python " + (& $pyExe @pyArgs -c 'import sys; print(sys.version.split()[0])'))

# ---------------------------------------------------------------- 2. Git
Step 2 'Git for Windows (Claude Code uses its Git Bash)'
if (Get-Command git -ErrorAction SilentlyContinue) {
    Ok ((git --version) -replace '^git version ', 'Git ')
} else {
    Warn 'Git is not installed. Claude Code on Windows needs it.'
    if (Ask '  Install Git now with winget?') { Install-WithWinget 'Git.Git' @() }
    if (Get-Command git -ErrorAction SilentlyContinue) { Ok 'Git installed (restart Claude Code so it can see it)' }
    else { $Problems.Add('Install Git from https://git-scm.com/download/win, then restart Claude Code.') }
}

# ---------------------------------------------------------------- 3. Virtual environment
Step 3 'Python environment and packages (first time takes a minute)'
if ((Test-Path '.venv') -and -not (Test-Path $VenvPy)) {
    # A .venv copied from a Mac has bin/ instead of Scripts\ and cannot run here.
    Warn '.venv is not a Windows environment (copied from a Mac?). Rebuilding it.'
    Remove-Item -Recurse -Force '.venv'
}
if (-not (Test-Path $VenvPy)) {
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Fail 'Could not create .venv.' }
}
& $VenvPy -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Fail 'Package install failed. Check your internet connection and run setup again.' }
Ok 'Packages installed in .venv'

# ---------------------------------------------------------------- 4. .env keys
Step 4 'API keys (.env)'
if (-not (Test-Path $EnvFile)) {
    Copy-Item '.env.example' $EnvFile
    Ok 'Created .env from .env.example'
} else {
    Ok 'Using your existing .env (nothing overwritten)'
}

function Get-EnvValue($name) {
    foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
        if ($line -match "^\s*$name\s*=\s*(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return ''
}
function Set-EnvValue($name, $value) {
    $lines = [IO.File]::ReadAllLines($EnvFile)
    $found = $false
    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match "^\s*$name\s*=") { $lines[$i] = "$name=$value"; $found = $true }
    }
    if (-not $found) { $lines += "$name=$value" }
    # UTF-8 without BOM, LF endings: what python-dotenv and the Mac expect.
    [IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

$keys = @(
    @{ Name = 'AIRROI_API_KEY';   Why = 'competitor comps';  Where = 'https://www.airroi.com/api/developer/activate' },
    @{ Name = 'GEMINI_API_KEY';   Why = 'photo scoring';     Where = 'https://aistudio.google.com/apikey' },
    @{ Name = 'HOSPITABLE_TOKEN'; Why = 'your listings (Hospitable users only; press Enter to skip)';
       Where = 'my.hospitable.com > Apps > API access > Platform token' }
)
foreach ($k in $keys) {
    $have = Get-EnvValue $k.Name
    if (-not $have -and $k.Name -eq 'HOSPITABLE_TOKEN') { $have = Get-EnvValue 'HOSPITABLE_API_KEY' }
    if ($have) { Ok "$($k.Name) is set"; continue }
    if ($NoPrompt) { Warn "$($k.Name) is empty ($($k.Why))"; continue }
    Write-Host ""
    Write-Host "  $($k.Name) - $($k.Why)"
    Write-Host "  Get it here: $($k.Where)"
    $val = (Read-Host '  Paste it and press Enter').Trim().Trim('"').Trim("'")
    if ($val) { Set-EnvValue $k.Name $val; Ok "$($k.Name) saved to .env" }
    else { Warn "$($k.Name) skipped. You can add it to .env later and rerun setup." }
}

# ---------------------------------------------------------------- 5. Tests
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

# ---------------------------------------------------------------- 6. Key check
Step 6 'Checking your keys (free, read-only)'
& $VenvPy scripts\check_keys.py
if ($LASTEXITCODE -ne 0) { $Problems.Add('Fix the keys marked !! in .env (open it with Notepad), then rerun setup.') }

# ---------------------------------------------------------------- Done
Write-Host ""
if ($Problems.Count -eq 0) {
    Write-Host '  ALL SET.' -ForegroundColor Green
} else {
    Write-Host '  ALMOST THERE. Fix these, then run setup again:' -ForegroundColor Yellow
    foreach ($p in $Problems) { Write-Host "   - $p" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host '  Next: open Claude Code in this folder and say:'
Write-Host '    "Optimize my <listing name> for <season>."'
Write-Host "  Reports land on your Desktop in the 'Listing Optimizer' folder."
Write-Host ""
if ($Problems.Count -gt 0) { exit 2 }
exit 0
