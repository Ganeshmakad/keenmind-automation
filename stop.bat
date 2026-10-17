@echo off
setlocal enabledelayedexpansion
title Keenmind Automation Engine - Stop Server
color 0c

echo ========================================================================
echo       KEENMIND AUDIO ^& VIDEO AUTOMATION ENGINE - SHUTDOWN
echo ========================================================================
echo.

set "STUDIO_PORT=9876"
if exist ".env" (
    for /f "tokens=1,2 delims==" %%a in ('type .env 2^>nul ^| findstr /i "^PORT="') do (
        set "STUDIO_PORT=%%b"
    )
)

echo Searching for running Keenmind Python server on port !STUDIO_PORT!...
set "FOUND=0"

for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r ":!STUDIO_PORT! .*LISTENING"') do (
    for /f "usebackq tokens=1 delims=," %%m in (`tasklist /fi "PID eq %%a" /fo csv /nh 2^>nul`) do (
        set "PROC_NAME=%%~m"
        if /i "!PROC_NAME!"=="python.exe" (
            echo   - Terminating Keenmind server (PID: %%a)...
            taskkill /F /PID %%a >nul 2>&1
            set "FOUND=1"
        ) else if /i "!PROC_NAME!"=="py.exe" (
            echo   - Terminating Keenmind server (PID: %%a)...
            taskkill /F /PID %%a >nul 2>&1
            set "FOUND=1"
        ) else (
            echo   - Protecting external process !PROC_NAME! (PID: %%a) - not terminating.
        )
    )
)

echo.
if "!FOUND!"=="1" (
    echo [SUCCESS] Keenmind Automation Engine was stopped cleanly.
) else (
    echo [INFO] No active Keenmind Python server found on port !STUDIO_PORT!.
)

echo.
echo ========================================================================
pause
