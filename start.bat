@echo off
setlocal enabledelayedexpansion
title Keenmind Automation Engine - Studio Server
color 0a

cd /d "%~dp0"

:: 1. Detect Python interpreter (prefer local venv, fallback to system python)
set "PY_EXEC="
if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_EXEC=%~dp0venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY_EXEC=python"
    ) else (
        where py >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY_EXEC=py"
        )
    )
)

if not defined PY_EXEC (
    echo.
    echo ========================================================================
    echo  [ERROR] Python or Virtual Environment not found!
    echo ========================================================================
    echo  Please run setup.bat first to configure your environment.
    echo ========================================================================
    echo.
    pause
    exit /b 1
)

:: 2. Add local portable bin directory to PATH if present
if exist "%~dp0bin" (
    set "PATH=%~dp0bin;%PATH%"
)

:: 3. Check for updates from GitHub (Zero tokens, exits in <0.5s if up-to-date)
if exist "%~dp0scripts\sync_updates.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sync_updates.ps1"
)

:: 4. Run the universal Python server launcher
"!PY_EXEC!" run_server.py

:: 4. If execution stopped unexpectedly, keep window open so the error is visible!
if !errorlevel! neq 0 (
    echo.
    echo ========================================================================
    echo  [NOTICE] The server has stopped. (Exit code: !errorlevel!)
    echo ========================================================================
    echo.
    pause
)
