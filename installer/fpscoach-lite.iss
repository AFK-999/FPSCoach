; ============================================================================
; FPS Coach - LITE installer (Inno Setup 6)
; Foundation build: NO AI models or runtime bundled. The user installs Ollama
; and pulls a model themselves; the app auto-pulls on first chat.
;
; Compiled by scripts\build.ps1 -Flavor lite, which passes:
;   /DAppVersion=<x.y.z>  /DProjectRoot=<path to repo>
; The frozen app is expected at <ProjectRoot>\dist\FPSCoach (from PyInstaller).
; ============================================================================
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef ProjectRoot
  #define ProjectRoot ".."
#endif

[Setup]
AppName=FPS Coach
AppVersion={#AppVersion}
AppPublisher=FPS Coach
DefaultDirName={autopf}\FPS Coach
DefaultGroupName=FPS Coach
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=FPSCoach-Setup-lite-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Files]
Source: "{#ProjectRoot}\dist\FPSCoach\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
; No \ollama or \models here - that is the point of the lite build.

[Icons]
Name: "{group}\FPS Coach"; Filename: "{app}\FPSCoach.exe"
Name: "{autodesktop}\FPS Coach"; Filename: "{app}\FPSCoach.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\FPSCoach.exe"; Description: "Launch FPS Coach"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=This is the lightweight FPS Coach. For AI chat, install Ollama separately; the app will pull the model automatically on first use.
