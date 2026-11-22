@echo off
title Build Fortnite Hit Tracker EXE
cd /d "%~dp0"

echo ============================================================
echo  Fortnite Hit Tracker - EXE builder
echo ============================================================
echo.
echo This builds a single standalone file:
echo    dist\FortniteHitTracker.exe
echo No Python needed to RUN the exe - only to build it.
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found on this PC.
    echo Install it from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

echo Installing/updating PyInstaller (one-time, needs internet)...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo.
    echo PyInstaller install failed - check your internet connection
    echo and run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo Building the exe...
python -m PyInstaller --onefile --console --name FortniteHitTracker HitTracker.py
if errorlevel 1 (
    echo.
    echo The build failed - the messages above should say why.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  Your exe is here:
echo    %CD%\dist\FortniteHitTracker.exe
echo ============================================================
echo.
pause
