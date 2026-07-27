@echo off
REM ════════════════════════════════════════════════════════════════
REM  Connectivity Agent — build a standalone .exe
REM
REM  Prerequisites (one-time setup):
REM    pip install pyinstaller openpyxl Pillow
REM
REM  Run this script from the project folder:
REM    build.bat
REM
REM  Output:
REM    dist\ConnectivityAgent.exe   ← the single file you distribute
REM
REM  The .exe includes Python, tkinter, openpyxl, Pillow, and all
REM  core modules. The target machine needs NOTHING installed — not
REM  even Python.
REM ════════════════════════════════════════════════════════════════

echo.
echo Cleaning previous build...
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist
if exist ConnectivityAgent.spec del /q ConnectivityAgent.spec

echo.
echo Building ConnectivityAgent.exe ...
echo (first build can take 1-3 minutes)
echo.

pyinstaller ^
  --onefile ^
  --windowed ^
  --name ConnectivityAgent ^
  --icon assets\LP_app.ico ^
  --add-data "assets;assets" ^
  --hidden-import openpyxl ^
  --hidden-import openpyxl.styles ^
  --hidden-import openpyxl.utils ^
  --collect-all openpyxl ^
  main.py

echo.
if exist "dist\ConnectivityAgent.exe" (
    echo ════════════════════════════════════════════════
    echo  BUILD SUCCESSFUL
    echo  dist\ConnectivityAgent.exe
    echo ════════════════════════════════════════════════
    echo.
    echo  To distribute: copy dist\ConnectivityAgent.exe
    echo  to any Windows machine — no Python needed.
    echo  Only requirement on the target machine:
    echo    * Internet access to reach the sensor URL
    echo      (agent.electricimp.com)
) else (
    echo BUILD FAILED. Check the output above for errors.
)
pause
