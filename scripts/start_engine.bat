@echo off
REM Start the AI engine manually (outside Maya), e.g. for debugging.
REM Maya's "Connect" (or auto-connect) finds it through the engine session file.
REM Extra arguments are passed to the engine, e.g.:  start_engine.bat --log-level DEBUG
setlocal
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv_engine\Scripts\python.exe"
if not exist "%PY%" (
    echo Engine environment not found at "%PY%".
    echo Run:  powershell -ExecutionPolicy Bypass -File "%ROOT%\scripts\setup_engine.ps1"
    exit /b 1
)
REM Clear variables that could make the venv load another Python's libraries.
set "PYTHONHOME="
set "PYTHONPATH=%ROOT%"
set "PYTHONNOUSERSITE=1"
cd /d "%ROOT%"
echo Starting Maya AI Toolkit engine (Ctrl+C to stop)...
"%PY%" -m ai_engine.main %*
exit /b %ERRORLEVEL%
