@echo off
rem ============================================================================
rem Apply the FPS Coach changes patch.
rem
rem HOW TO USE:
rem   1. Put this file AND "FPSCoach-all-changes.patch" in your FPS Coach
rem      project root (the folder that contains pyproject.toml).
rem   2. Double-click this file.
rem
rem It prefers "git am" (applies as a real commit). If that cannot apply cleanly
rem it falls back to "git apply" (changes your files without committing).
rem ============================================================================
setlocal
cd /d "%~dp0"

set "PATCH=FPSCoach-all-changes.patch"

rem --- sanity checks ----------------------------------------------------------
where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is not installed or not on PATH. Install Git for Windows first.
  echo        https://git-scm.com/download/win
  pause
  exit /b 1
)
if not exist "pyproject.toml" (
  echo ERROR: Run this from the FPS Coach project root ^(where pyproject.toml is^).
  pause
  exit /b 1
)
if not exist "%PATCH%" (
  echo ERROR: Could not find "%PATCH%" next to this file.
  echo        Put the patch in this folder and try again.
  pause
  exit /b 1
)
if not exist ".git" (
  echo ERROR: This folder is not a git repository ^(no .git folder^).
  echo        Clone the repo with git first, then apply the patch.
  pause
  exit /b 1
)

rem --- check it applies before touching anything ------------------------------
echo Checking whether the patch applies cleanly...
git apply --check "%PATCH%" 2>nul
if errorlevel 1 (
  echo.
  echo WARNING: The patch does not apply cleanly to this repo. It may already be
  echo          applied, or your files differ from the expected base.
  echo          Nothing has been changed.
  pause
  exit /b 1
)

rem --- apply as a real commit first -------------------------------------------
echo Applying patch as a commit ^(git am^)...
git am "%PATCH%"
if not errorlevel 1 (
  echo.
  echo SUCCESS: Patch applied as a commit. Review with:  git log -1
  pause
  exit /b 0
)

rem git am failed/aborted; clean up and fall back
git am --abort >nul 2>nul
echo git am did not complete; falling back to a plain apply...
git apply "%PATCH%"
if errorlevel 1 (
  echo.
  echo ERROR: Could not apply the patch. Nothing was changed.
  pause
  exit /b 1
)

echo.
echo SUCCESS: Patch applied to your working files ^(not committed^).
echo Review with:  git status   then commit when ready.
pause
exit /b 0
