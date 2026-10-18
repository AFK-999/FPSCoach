@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo FPS Coach has not been set up yet. Run "Setup and Run.bat" first.
  pause
  exit /b 1
)

:: Run the python module directly from the virtual environment
".venv\Scripts\python.exe" -m fps_coach

if errorlevel 1 pause