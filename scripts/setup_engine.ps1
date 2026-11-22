<#
.SYNOPSIS
    Creates the AI engine's isolated Python virtual environment.

.DESCRIPTION
    The engine NEVER runs in Maya's bundled Python. This script creates
    <toolkit>\.venv_engine from a standalone CPython (3.10 or newer) and
    installs requirements_engine.txt into it. Maya is not modified.

.PARAMETER PythonExe
    Optional path to the Python used to create the venv. Default: the newest
    3.10+ found through the "py" launcher, then "python" on PATH.

.PARAMETER VenvDir
    Optional venv location. Default: <toolkit>\.venv_engine (auto-detected by the plugin).
    If you choose another folder, set it in the toolkit Settings dialog.

.PARAMETER Force
    Delete and recreate an existing venv.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_engine.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_engine.ps1 -PythonExe "C:\Python311\python.exe"
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$VenvDir = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $VenvDir) { $VenvDir = Join-Path $Root ".venv_engine" }
$Requirements = Join-Path $Root "requirements_engine.txt"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Test-IsMayaPython([string]$path) {
    $leaf = [IO.Path]::GetFileNameWithoutExtension($path).ToLower()
    return ($leaf -in @("maya", "mayapy", "mayabatch")) -or ($path -match "\\Autodesk\\Maya")
}

function Get-PythonVersion([string[]]$cmd) {
    try {
        $exe = $cmd[0]; $rest = @(); if ($cmd.Length -gt 1) { $rest = $cmd[1..($cmd.Length - 1)] }
        $out = & $exe @rest -c "import sys; print('%d.%d|%s' % (sys.version_info[0], sys.version_info[1], sys.executable))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return $out.Trim() }
    } catch { }
    return $null
}

# ---------------------------------------------------------------- locate Python
Write-Step "Locating a standalone Python 3.10+"
$candidates = @()
if ($PythonExe) { $candidates += ,@($PythonExe) }
else {
    foreach ($v in @("3.12", "3.11", "3.10")) { $candidates += ,@("py", "-$v") }
    $candidates += ,@("python")
    $candidates += ,@("python3")
}
$chosen = $null
foreach ($cmd in $candidates) {
    $info = Get-PythonVersion $cmd
    if (-not $info) { continue }
    $parts = $info.Split("|"); $ver = [version]$parts[0]; $exePath = $parts[1]
    if (Test-IsMayaPython $exePath) { Write-Host "   skipping Maya interpreter: $exePath"; continue }
    if ($ver -lt [version]"3.10") { Write-Host "   skipping Python $ver (need 3.10+): $exePath"; continue }
    $chosen = $exePath; Write-Host "   using Python $ver at $exePath"; break
}
if (-not $chosen) { Fail "No standalone Python 3.10+ found. Install one from https://www.python.org/downloads/windows/ (not Maya's mayapy) and re-run, or pass -PythonExe." }

# ---------------------------------------------------------------- create venv
if (Test-Path $VenvDir) {
    if ($Force) { Write-Step "Removing existing venv $VenvDir"; Remove-Item -Recurse -Force $VenvDir }
    else { Write-Host "   venv already exists at $VenvDir (use -Force to recreate)" }
}
if (-not (Test-Path $VenvDir)) {
    Write-Step "Creating virtual environment at $VenvDir"
    & $chosen -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" }
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { Fail "venv python not found at $VenvPython" }

Write-Step "Upgrading pip"
& $VenvPython -m pip install --upgrade pip --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" }

Write-Step "Installing engine requirements"
& $VenvPython -m pip install -r $Requirements --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "requirements install failed" }

# ---------------------------------------------------------------- verify
Write-Step "Verifying the engine can start"
Push-Location $Root
try {
    $env:PYTHONPATH = $Root
    $ver = & $VenvPython -m ai_engine.main --version
    if ($LASTEXITCODE -ne 0) { Fail "engine self-check failed" }
    Write-Host "   engine version $ver"
} finally { Pop-Location; Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "Engine environment ready: $VenvPython" -ForegroundColor Green
Write-Host "Next: start Maya, open the toolkit from the MayaAIToolkit shelf and click 'Start Engine'."
if ($VenvDir -ne (Join-Path $Root ".venv_engine")) {
    Write-Host "Custom venv location: set 'Engine Python' to $VenvPython in the toolkit Settings." -ForegroundColor Yellow
}
