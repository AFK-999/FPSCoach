; ============================================================================
; FPS Coach - FULL installer (Inno Setup 6)
; Embeds the Ollama runtime + Qwen 3 1.7B weights so the AI works out of the box.
;
; Compiled by scripts\build.ps1 -Flavor full, which passes:
;   /DAppVersion=<x.y.z>  /DProjectRoot=<path to repo>
; Expects the frozen app at <ProjectRoot>\dist\FPSCoach and staged AI assets at
; <ProjectRoot>\build\ai_assets (produced by fetch_ai_assets.ps1). The spec also
; folds those assets into the frozen app under {app}\ollama and {app}\models.
;
; NOTE: Confirm redistribution licensing for Ollama + Qwen before shipping.
; ============================================================================
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef ProjectRoot
  #define ProjectRoot ".."
#endif

[Setup]
AppName=FPS Coach (Full)
AppVersion={#AppVersion}
AppPublisher=FPS Coach
DefaultDirName={autopf}\FPS Coach
DefaultGroupName=FPS Coach
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=FPSCoach-Setup-full-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Files]
Source: "{#ProjectRoot}\dist\FPSCoach\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
; Embedded AI runtime + model weights (also staged into the frozen app by the spec):
Source: "{#ProjectRoot}\build\ai_assets\ollama\*"; DestDir: "{app}\ollama"; Flags: recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\build\ai_assets\models\*"; DestDir: "{app}\models"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FPS Coach"; Filename: "{app}\FPSCoach.exe"
Name: "{autodesktop}\FPS Coach"; Filename: "{app}\FPSCoach.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
; Prepare the embedded Ollama + model on first run, then offer to launch.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\setup_embedded_ai.ps1"" -AppDir ""{app}"""; \
  Flags: runhidden
Filename: "{app}\FPSCoach.exe"; Description: "Launch FPS Coach"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=This is the full FPS Coach. The Ollama runtime and the Qwen 3 1.7B model are included - the AI works offline out of the box.
