# Build & packaging

FPS Coach ships in two installer flavors. Both are produced by the same script
with a different `-Flavor` argument.

| Flavor | AI runtime/model | Installer size | Who it's for |
|--------|------------------|----------------|--------------|
| **lite** | None bundled. User installs Ollama; the app auto-pulls the model on first chat. | Small | Users who already have Ollama, or want the smallest download. |
| **full** | Embeds the Ollama runtime **and** the Qwen 3 1.7B weights. AI works offline out of the box. | Large | Users who want zero setup. |

## Prerequisites (build machine, Windows x64)

- Python 3.11 (`py -3.11`)
- Visual Studio 2022 C++ Build Tools (for the native `fpscoach_native` module)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) with `iscc` on `PATH`
- For the **full** build: just an internet connection. `scripts\fetch_ai_assets.ps1`
  downloads the Ollama Windows runtime and pulls `qwen3:1.7b` automatically - no
  pre-installed Ollama and no environment variables required.

## Installing without the native core

`CMakeLists.txt` skips the C++ core when `FPSCOACH_SKIP_NATIVE` is set to anything other
than `0` or empty. The wheel then contains only the Python package, no compiler or Windows
SDK is needed, and `fps_coach.backend` falls back to the portable analyzer:

```powershell
$env:FPSCOACH_SKIP_NATIVE = "1"
pip install -e ".[dev,recorder]"
```

Use this when the native link step fails on the machine (`LNK2001: unresolved external
symbol __imp__Py...`), which usually means the Python version and MSVC/import library do
not match. Unset the variable (or set it to `0`) to build the native core again.

## Build

Double-click one of these in the project root, or run from a terminal:

```
Build-Lite.bat      ::  -> dist\FPSCoach-Setup-lite-<version>-x64.exe
Build-Full.bat      ::  -> dist\FPSCoach-Setup-full-<version>-x64.exe
```

Equivalent direct calls:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Flavor lite
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Flavor full
```

### What the script does
1. Creates/updates `.venv` and installs the project + PyInstaller.
2. Stamps `python\fps_coach\build_info.py` with the flavor.
3. **full only:** `scripts\fetch_ai_assets.ps1` automatically downloads the
   Ollama Windows runtime and pulls `qwen3:1.7b` into an isolated store under
   `build\ai_assets\` (only that one model is bundled, nothing else).
4. **full only:** the build **aborts with a fatal error** if the runtime or the
   model weights are missing/incomplete - it never silently ships a Lite-like
   package.
5. Freezes the app with PyInstaller (`installer\fpscoach.spec`). The AI assets
   are **not** bundled here - the full Inno Setup script is the single place they
   are copied in, so the ~1 GB of weights is processed exactly once.
6. Compiles the matching Inno Setup script in `installer\`.

### Optional: build fully offline
The automatic download needs no setup. If you are offline, point these at
pre-downloaded folders to skip the corresponding download (they are still
verified before the build continues):

```
set FPSCOACH_OLLAMA_SRC=C:\path\to\ollama-windows
set FPSCOACH_QWEN_SRC=C:\path\to\qwen3-1.7b-model
```

> **Licensing:** confirm redistribution terms for the Ollama runtime and the
> Qwen 3 1.7B weights before publishing the full installer.

## Related runtime behavior
- **Auto-pull:** on the lite build, `ai_chat.py` pulls the configured model the
  first time the user chats (disable with `auto_pull=False` / env `FPSCOACH_AUTO_PULL=0`).
- **Self-repair:** `env_repair.ensure_environment()` runs at startup and repairs
  the editable install if the project folder was moved or renamed.
- **AI pre-config:** `config\ai_profile.toml` holds the persona, instructions,
  and recommendations injected into every chat.
