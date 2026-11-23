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

.PARAMETER WithSam2
    Also install the optional SAM 2.1 backend: PyTorch (CUDA build) + sam2.
    Downloads several GB. Model weights are NOT downloaded here; install them
    from AI Studio > Model Manager.

.PARAMETER CudaVersion
    PyTorch wheel flavour used with -WithSam2: cu118, cu121, cu124 (default) or cpu.
    Pick one supported by your NVIDIA driver (see "nvidia-smi", top right "CUDA Version").

.PARAMETER CaBundle
    Optional PEM file with your company's root CA (networks that inspect SSL). Usually not
    needed: if pip cannot verify certificates, the script automatically retries with the
    Windows certificate store (Root + intermediate CA stores) combined with pip's own list.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_engine.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_engine.ps1 -PythonExe "C:\Python311\python.exe"
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_engine.ps1 -WithSam2 -CudaVersion cu124
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$VenvDir = "",
    [switch]$Force,
    [switch]$WithSam2,
    [ValidateSet("cu118", "cu121", "cu124", "cpu")]
    [string]$CudaVersion = "cu124",
    [string]$CaBundle = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $VenvDir) { $VenvDir = Join-Path $Root ".venv_engine" }
$Requirements = Join-Path $Root "requirements_engine.txt"
$Sam2Requirements = Join-Path $Root "requirements_sam2.txt"

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

function ConvertTo-Pem($cert) {
    $b64 = [Convert]::ToBase64String($cert.RawData)
    $lines = for ($i = 0; $i -lt $b64.Length; $i += 64) { $b64.Substring($i, [Math]::Min(64, $b64.Length - $i)) }
    return "-----BEGIN CERTIFICATE-----`n" + ($lines -join "`n") + "`n-----END CERTIFICATE-----`n"
}

# Networks with SSL inspection re-sign HTTPS with a company CA that IT installs in the Windows
# certificate store. pip uses its own CA list, so it fails there. This builds a PEM bundle with
# pip's list + the Windows Root and intermediate CA stores (+ -CaBundle) and points pip at it.
# Verification stays on; nothing is written outside the venv folder.
function Enable-WindowsCertificates([string]$python) {
    $bundle = Join-Path $VenvDir "windows_ca_bundle.pem"
    $parts = New-Object System.Collections.Generic.List[string]
    try {
        $certifi = & $python -c "import pip._vendor.certifi as c; print(c.where())" 2>$null
        if ($LASTEXITCODE -eq 0 -and $certifi -and (Test-Path $certifi.Trim())) {
            $parts.Add([IO.File]::ReadAllText($certifi.Trim()))
        }
    } catch { }
    $seen = @{}
    foreach ($store in @("Cert:\CurrentUser\Root", "Cert:\LocalMachine\Root", "Cert:\CurrentUser\CA",
                         "Cert:\LocalMachine\CA", "Cert:\LocalMachine\AuthRoot")) {
        foreach ($cert in (Get-ChildItem $store -ErrorAction SilentlyContinue)) {
            if (-not $seen.ContainsKey($cert.Thumbprint)) {
                $seen[$cert.Thumbprint] = $true
                $parts.Add((ConvertTo-Pem $cert))
            }
        }
    }
    if ($CaBundle) {
        if (-not (Test-Path $CaBundle)) { Fail "-CaBundle file not found: $CaBundle" }
        $parts.Add([IO.File]::ReadAllText($CaBundle))
    }
    [IO.File]::WriteAllText($bundle, ($parts -join "`n"), [Text.Encoding]::ASCII)
    $env:PIP_CERT = $bundle
    Write-Host "   using $($seen.Count) certificate(s) from the Windows store: $bundle"
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

# Can pip's own certificate list reach PyPI? "pip install --upgrade pip" exits 0 even when it
# cannot download (pip is already installed), so test the connection explicitly.
$TlsProbe = @"
import ssl, sys, urllib.request
try:
    import pip._vendor.certifi as certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    ctx = ssl.create_default_context()
try:
    urllib.request.urlopen('https://pypi.org/simple/pip/', timeout=30, context=ctx).close()
except ssl.SSLError as exc:
    print('certificate: %s' % exc); sys.exit(3)
except Exception as exc:
    reason = getattr(exc, 'reason', None)
    if isinstance(reason, ssl.SSLError):
        print('certificate: %s' % reason); sys.exit(3)
    print('network: %s' % exc); sys.exit(2)
"@

# Run pip; on failure retry once with the Windows certificate bundle (if not already in use).
function Invoke-Pip([string]$what, [string[]]$pipArgs) {
    & $VenvPython -m pip @pipArgs --disable-pip-version-check
    if ($LASTEXITCODE -ne 0 -and -not $env:PIP_CERT) {
        Write-Host "   $what failed. Retrying with the Windows certificate store (SSL-inspecting networks)." -ForegroundColor Yellow
        Enable-WindowsCertificates $VenvPython
        & $VenvPython -m pip @pipArgs --disable-pip-version-check
    }
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (network or certificate problem; see the pip error above)" }
}

if ($CaBundle) {
    Enable-WindowsCertificates $VenvPython
} else {
    Write-Step "Checking the HTTPS connection to PyPI"
    $probeFile = Join-Path $VenvDir "tls_probe.py"
    [IO.File]::WriteAllText($probeFile, $TlsProbe, [Text.Encoding]::ASCII)
    try {
        $probe = & $VenvPython $probeFile
        $probeCode = $LASTEXITCODE
    } catch {
        $probe = "$_"; $probeCode = 2
    } finally { Remove-Item $probeFile -ErrorAction SilentlyContinue }
    if ($probeCode -eq 3) {
        Write-Host "   pip's certificates are not trusted on this network ($probe)." -ForegroundColor Yellow
        Write-Host "   Using the Windows certificate store instead (SSL inspection)." -ForegroundColor Yellow
        Enable-WindowsCertificates $VenvPython
    } elseif ($probeCode -ne 0) {
        Write-Host "   warning: $probe" -ForegroundColor Yellow
    } else {
        Write-Host "   OK"
    }
}
Write-Step "Upgrading pip"
Invoke-Pip "pip upgrade" @("install", "--upgrade", "pip")

Write-Step "Installing engine requirements"
Invoke-Pip "requirements install" @("install", "-r", $Requirements)

# ---------------------------------------------------------------- optional SAM 2
if ($WithSam2) {
    Write-Step "Installing PyTorch ($CudaVersion) for SAM 2 - this is a large download"
    Invoke-Pip "PyTorch install (if it is not a certificate problem, try another -CudaVersion)" @(
        "install", "torch>=2.5.1", "torchvision>=0.20.1", "--index-url", "https://download.pytorch.org/whl/$CudaVersion")
    Write-Step "Installing SAM 2"
    $env:SAM2_BUILD_CUDA = "0"   # skip the optional CUDA extension (needs a compiler)
    try {
        Invoke-Pip "SAM 2 install" @("install", "-r", $Sam2Requirements)
    } finally { Remove-Item Env:\SAM2_BUILD_CUDA -ErrorAction SilentlyContinue }
    $cuda = & $VenvPython -c "import torch, sam2; print('torch %s, CUDA available: %s' % (torch.__version__, torch.cuda.is_available()))"
    if ($LASTEXITCODE -ne 0) { Fail "SAM 2 import check failed" }
    Write-Host "   $cuda"
}

# ---------------------------------------------------------------- verify
Write-Step "Verifying the engine can start"
Push-Location $Root
try {
    $env:PYTHONPATH = $Root
    $ver = & $VenvPython -m ai_engine.main --version
    if ($LASTEXITCODE -ne 0) { Fail "engine self-check failed" }
    Write-Host "   engine version $ver"
} finally { Pop-Location; Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue }

Remove-Item Env:\PIP_CERT -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Engine environment ready: $VenvPython" -ForegroundColor Green
Write-Host "Next: start Maya, open the toolkit from the MayaAIToolkit shelf and click 'Start Engine'."
if ($WithSam2) { Write-Host "Then install SAM 2.1 weights from AI Studio > Model Manager." }
else { Write-Host "Optional: re-run with -WithSam2 to add the SAM 2.1 AI backend (GPU recommended)." }
if ($VenvDir -ne (Join-Path $Root ".venv_engine")) {
    Write-Host "Custom venv location: set 'Engine Python' to $VenvPython in the toolkit Settings." -ForegroundColor Yellow
}
