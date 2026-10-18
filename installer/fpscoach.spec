# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for FPS Coach (shared by the lite and full installers).
#
# Build the frozen app with:
#   .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean installer\fpscoach.spec
#
# The build flavor ("lite" / "full") is passed via the FPSCOACH_BUILD_FLAVOR env
# var by scripts\build.ps1; it only affects bundled data, not the exe itself.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(os.getcwd())
FLAVOR = os.environ.get("FPSCOACH_BUILD_FLAVOR", "lite")

# Ship the AI pre-configuration and (for full builds) staged AI assets.
datas = [
    (str(ROOT / "config" / "ai_profile.toml"), "config"),
]
if FLAVOR == "full":
    assets = ROOT / "build" / "ai_assets"
    if (assets / "ollama").exists():
        datas.append((str(assets / "ollama"), "ollama"))
    if (assets / "models").exists():
        datas.append((str(assets / "models"), "models"))

block_cipher = None

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT / "python")],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("fps_coach"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FPSCoach",
    console=False,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="FPSCoach",
)
