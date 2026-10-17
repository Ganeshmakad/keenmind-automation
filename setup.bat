@echo off
setlocal enabledelayedexpansion
title Keenmind Automation Engine - Setup
color 0b

echo ========================================================================
echo        KEENMIND AUDIO ^& VIDEO AUTOMATION ENGINE - SETUP WIZARD
echo ========================================================================
echo.
echo  This wizard will check your system and configure all dependencies:
echo    1. Python 3.10+ check
echo    2. FFmpeg audio/video engine check
echo    3. Python Virtual Environment (venv) creation
echo    4. Python package dependencies installation
echo    5. Environment configuration (.env)
echo.
echo ========================================================================
echo.

cd /d "%~dp0"

:: ---------------------------------------------------------
:: 1. CHECK PYTHON
:: ---------------------------------------------------------
echo [1/5] Checking Python installation...
set "PY_CMD="

python --version >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=python"
) else (
    py --version >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY_CMD=py"
    )
)

if not defined PY_CMD (
    echo.
    echo [ERROR] Python was not found in your system PATH!
    echo.
    echo Keenmind Automation requires Python 3.10 or higher.
    echo.
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        set /p INSTALL_PY="Would you like to install Python 3.11 automatically via winget? (Y/N): "
        if /i "!INSTALL_PY!"=="Y" (
            echo Installing Python 3.11...
            winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
            echo.
            echo [IMPORTANT] Python has been installed. Please CLOSE this window,
            echo reopen it, and run setup.bat again to refresh environment PATH.
            pause
            exit /b 0
        )
    )
    echo Opening Python download page in your browser...
    start https://www.python.org/downloads/
    echo Please install Python 3.10+ and check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('!PY_CMD! --version 2^>^&1') do set "PY_VER=%%v"
echo   - Found: !PY_VER!

:: ---------------------------------------------------------
:: 2. CHECK FFMPEG
:: ---------------------------------------------------------
echo.
echo [2/5] Checking FFmpeg (Required for video audio extraction and remuxing)...

set "FFMPEG_FOUND=0"
ffmpeg -version >nul 2>&1
if !errorlevel! equ 0 (
    set "FFMPEG_FOUND=1"
    echo   - Found system FFmpeg in PATH.
) else (
    if exist "%~dp0bin\ffmpeg.exe" (
        set "FFMPEG_FOUND=1"
        echo   - Found local portable FFmpeg in .\bin\ffmpeg.exe
    )
)

if "!FFMPEG_FOUND!"=="0" (
    echo   - [WARNING] FFmpeg is not found!
    echo.
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo   Installing FFmpeg automatically via winget...
        winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
        if !errorlevel! equ 0 (
            echo   - [OK] FFmpeg installed successfully via winget.
            set "FFMPEG_FOUND=1"
        )
    )
    
    if "!FFMPEG_FOUND!"=="0" (
        echo.
        echo   Alternatively, you can download FFmpeg manually from:
        echo   https://www.gyan.dev/ffmpeg/builds/
        echo   and place ffmpeg.exe inside a 'bin' folder in this directory.
        echo.
        set /p CONT="Continue setup without FFmpeg for now? (Audio TTS will work, Video Voice Change requires FFmpeg) (Y/N): "
        if /i not "!CONT!"=="Y" (
            pause
            exit /b 1
        )
    )
)

:: ---------------------------------------------------------
:: 3. CREATE VIRTUAL ENVIRONMENT
:: ---------------------------------------------------------
echo.
echo [3/5] Setting up isolated Python Virtual Environment (venv)...
if not exist "venv\Scripts\activate.bat" (
    echo   - Creating venv...
    !PY_CMD! -m venv venv
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo   - Virtual environment created successfully.
) else (
    echo   - Virtual environment already exists.
)

:: ---------------------------------------------------------
:: 4. INSTALL REQUIREMENTS & HARDWARE ACCELERATION
:: ---------------------------------------------------------
echo.
echo [4/5] Installing project dependencies into venv...
call venv\Scripts\activate.bat

python -m pip install --upgrade pip --quiet
echo   - Installing packages from requirements.txt (FastAPI, Uvicorn, faster-whisper, etc.)...
pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo [ERROR] Dependency installation encountered errors.
    pause
    exit /b 1
)
echo   - Base dependencies installed successfully.

echo.
echo   - Checking for NVIDIA GPU hardware acceleration...
where nvidia-smi >nul 2>&1
if !errorlevel! equ 0 (
    echo     [GPU DETECTED] Dedicated NVIDIA graphics card found!
    echo     Native CTranslate2 CUDA acceleration enabled (Zero 2.8GB download required).
) else (
    echo     [INFO] Standard multi-core CPU mode active.
)

:: ---------------------------------------------------------
:: 5. ENVIRONMENT FILE & DIRECTORIES
:: ---------------------------------------------------------
echo.
echo [5/5] Checking configuration (.env) and working folders...

if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo   - Created .env from .env.example
        echo   - [NOTE] Open .env and insert your ELEVENLABS_API_KEY,
        echo     or enter it directly inside the Web Studio settings modal.
    )
) else (
    echo   - .env file already present.
)

if not exist "output" mkdir output
if not exist "cache" mkdir cache
if not exist "temp" mkdir temp
if not exist "temp\uploads" mkdir temp\uploads

echo.
echo ========================================================================
echo                   SETUP COMPLETE AND READY TO RUN!
echo ========================================================================
echo.
echo  To launch Keenmind Automation Engine and Web Studio:
echo     Double-click: start.bat
echo.
echo  To stop the engine at any time:
echo     Double-click: stop.bat
echo ========================================================================
echo.
pause
