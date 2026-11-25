@echo off
REM ============================================================================
REM  seedvr2.cmd - CLI launcher for the SeedVR2 Turbo desktop installer
REM ============================================================================
REM  Installed to <InstallDir>\seedvr2.cmd by setup.nsi, and the install dir is
REM  added to the current user's PATH, so `seedvr2 ...` works in any terminal
REM  (and for AI agents that shell out). It runs the bundled cli.py with the
REM  in-app bootstrap interpreter (same Python that carries the downloaded
REM  torch/runtime), NOT the system Python.
REM
REM  Layout after install + in-app "download manager" setup:
REM    <InstallDir>\app\bootstrap\python.exe        (bootstrap interpreter)
REM    <InstallDir>\app\app\integrated_app\cli.py   (CLI source, app/app two-level)
REM    <InstallDir>\app\model\                      (weights from download manager)
REM
REM  Keep this file ASCII-only: it is a .cmd parsed under the active code page.
REM ============================================================================
setlocal EnableExtensions

set "APPROOT=%~dp0app"
set "PYEXE=%APPROOT%\bootstrap\python.exe"
set "CLIPY=%APPROOT%\app\integrated_app\cli.py"

if not exist "%PYEXE%" (
    echo [seedvr2] ERROR: bootstrap interpreter not found: %PYEXE% >&2
    echo [seedvr2] Open the desktop app and finish "Download Manager" -^> runtime install first. >&2
    exit /b 3
)
if not exist "%CLIPY%" (
    echo [seedvr2] ERROR: CLI entry not found: %CLIPY% >&2
    exit /b 3
)

REM --- Environment: align with scripts/start_portable.py (offline + local caches) ---
set "PYTHONNOUSERSITE=1"
set "PYTHONUNBUFFERED=1"
set "HF_HUB_OFFLINE=1"
set "MODELSCOPE_OFFLINE=1"
set "HUGGINGFACE_HUB_CACHE=%APPROOT%\cache\huggingface"
set "MODELSCOPE_CACHE=%APPROOT%\cache\modelscope"
set "TORCH_HOME=%APPROOT%\cache\torch"
set "XDG_CACHE_HOME=%APPROOT%\cache"

"%PYEXE%" "%CLIPY%" %*
exit /b %ERRORLEVEL%
