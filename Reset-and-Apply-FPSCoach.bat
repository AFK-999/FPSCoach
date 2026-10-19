@echo off
rem ============================================================================
rem  FPS Coach - clean RESET + APPLY
rem
rem  Fixes a half-applied patch by overwriting your project with the correct,
rem  final files straight from FPSCoach-full-repo.zip (no git patch involved,
rem  so CRLF / partial-apply problems cannot happen).
rem
rem  HOW TO USE:
rem    1. Put this file AND "FPSCoach-full-repo.zip" in your project root
rem       (the folder that contains pyproject.toml).
rem    2. Double-click this file.
rem    3. When it finishes, run "Run.bat" (or "Setup and Run.bat").
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "ZIP=FPSCoach-full-repo.zip"

echo(
echo === FPS Coach: clean reset ^& apply ===
echo Project folder: "%CD%"
echo(

rem --- sanity checks ----------------------------------------------------------
if not exist "pyproject.toml" (
  echo ERROR: Run this from the FPS Coach project root ^(where pyproject.toml is^).
  pause
  exit /b 1
)
if not exist "%ZIP%" (
  echo ERROR: Could not find "%ZIP%" next to this file.
  echo        Download it from your workspace and put it in this folder.
  pause
  exit /b 1
)

rem --- 1. safety backup of the current folder (excluding heavy/junk dirs) ------
set "STAMP=%DATE:/=-%_%TIME::=-%"
set "STAMP=%STAMP: =0%"
set "BACKUP=..\FPSCoach-backup-%STAMP%"
echo Making a safety backup at "%BACKUP%" ...
robocopy "%CD%" "%BACKUP%" /E /XD ".git" ".venv" "build" "dist" "__pycache__" ".fpscoach-update-backups" /XF "%ZIP%" >nul
echo Backup done.
echo(

rem --- 2. extract the zip to a temp folder ------------------------------------
set "TMPX=%TEMP%\FPSCoach-apply-%RANDOM%"
echo Extracting "%ZIP%" ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Expand-Archive -LiteralPath '%CD%\%ZIP%' -DestinationPath '%TMPX%' -Force"
if errorlevel 1 (
  echo ERROR: Could not extract the zip.
  pause
  exit /b 1
)

rem The zip root may be the files directly, or a single FPSCoach\ subfolder.
set "SRC=%TMPX%"
if exist "%TMPX%\FPSCoach\pyproject.toml" set "SRC=%TMPX%\FPSCoach"
if not exist "%SRC%\pyproject.toml" (
  echo ERROR: The zip does not look like the FPS Coach project.
  rd /s /q "%TMPX%" 2>nul
  pause
  exit /b 1
)

rem --- 3. copy the correct files over, overwriting ----------------------------
echo Applying updated files ...
robocopy "%SRC%" "%CD%" /E /XD ".git" ".venv" "build" "dist" "__pycache__" ".fpscoach-update-backups" /XF "%ZIP%" "%~nx0" >nul
set "RC=%ERRORLEVEL%"
rd /s /q "%TMPX%" 2>nul

rem robocopy exit codes 0-7 are success; 8+ are real errors.
if %RC% GEQ 8 (
  echo ERROR: Copy failed ^(robocopy code %RC%^). Your backup is at "%BACKUP%".
  pause
  exit /b 1
)

echo(
echo === SUCCESS ===
echo The correct files are now in place. Quick check:
findstr /M /C:"build_style" "python\fps_coach\ui\styles.py" >nul 2>nul && echo   [OK] styles.py updated ^(new rounded theme^) || echo   [!!] styles.py NOT updated
findstr /M /C:"class SettingsPage" "python\fps_coach\ui\main_window.py" >nul 2>nul && echo   [OK] main_window.py updated ^(Settings page^) || echo   [!!] main_window.py NOT updated
echo(
echo Next:  run "Run.bat"  (or "Setup and Run.bat" if it mentions the venv).
echo A safety backup of your old folder is at: "%BACKUP%"
echo(
echo Optional - commit to git afterwards:
echo     git add -A
echo     git commit -m "Apply all FPS Coach updates (UI overhaul + features)"
echo     git push
echo(
pause
endlocal
