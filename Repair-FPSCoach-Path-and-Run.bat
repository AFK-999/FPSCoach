@echo off
rem Repairs stale absolute paths after the complete FPS Coach folder was moved.
setlocal
cd /d "%~dp0"

if not exist "pyproject.toml" (
  echo ERROR: Put this file beside pyproject.toml in the FPS Coach project folder.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: The local Python environment is missing.
  echo Run "Setup and Run.bat" instead.
  pause
  exit /b 1
)

echo Repairing FPS Coach for its current folder...
".venv\Scripts\python.exe" -m pip install --editable . --no-deps
if errorlevel 1 (
  echo.
  echo Repair failed. Run "Setup and Run.bat" to perform a complete rebuild.
  pause
  exit /b 1
)

rem Prefer this folder's source even if an older editable finder remains cached.
set "PYTHONPATH=%CD%\python;%PYTHONPATH%"
echo Repair complete. Starting FPS Coach...
".venv\Scripts\python.exe" -m fps_coach
if errorlevel 1 (
  echo.
  echo FPS Coach could not start. Run "Setup and Run.bat" for a complete rebuild.
  pause
  exit /b 1
)
