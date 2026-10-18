@echo off
rem Build the LITE FPS Coach installer (no AI models bundled).
rem Place beside pyproject.toml and double-click, or run from a terminal.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build.ps1" -Flavor lite
if errorlevel 1 (
  echo.
  echo Lite build failed. See the messages above.
  pause
  exit /b 1
)
echo.
echo Done. The installer is in the "dist" folder.
pause
