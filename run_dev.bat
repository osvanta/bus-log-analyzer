@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

rem pythonw: no console window beside the application. What a console would
rem show goes to osvanta_app.log; run "python app.py" to watch it live.
set "PYEXE="
if exist .venv\Scripts\pythonw.exe (
    set "PYEXE=.venv\Scripts\pythonw.exe"
) else (
    where pythonw >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Create .venv first or install Python and add it to PATH.
        pause
        exit /b 1
    )
    set "PYEXE=pythonw"
)

start "" "%PYEXE%" app.py
