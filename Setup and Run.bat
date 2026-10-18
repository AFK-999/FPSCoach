@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo Setup failed. Confirm Python 3.11 and Visual Studio 2022 C++ Build Tools are installed.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run.ps1"
