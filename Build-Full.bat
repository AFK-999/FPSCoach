@echo off
rem Build the FULL FPS Coach installer (embeds Ollama runtime + Qwen 3 1.7B).
rem Place beside pyproject.toml and double-click, or run from a terminal.
rem Optional: set FPSCOACH_OLLAMA_SRC and FPSCOACH_QWEN_SRC to pre-downloaded
rem folders to avoid pulling the model during the build.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build.ps1" -Flavor full
if errorlevel 1 (
  echo.
  echo Full build failed. See the messages above.
  pause
  exit /b 1
)
echo.
echo Done. The installer is in the "dist" folder.
pause
