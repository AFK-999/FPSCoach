# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for FPS Coach (shared by the lite and full installers).
#
# Build the frozen app with:
#   .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean installer\fpscoach.spec
#
# The build flavor ("lite" / "full") is passed via the FPSCOACH_BUILD_FLAVOR env
# var by scripts\build.ps1; it only affects the stamped build_info, not this spec.
#
# IMPORTANT: the embedded AI assets (Ollama runtime + Qwen weights) are NOT
# bundled here. They are copied into the install by the full Inno Setup script
# (installer\fpscoach-full.iss) as the single source of truth. Bundling them in
# both places would process ~1 GB of model blobs twice and bloat the build.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(os.getcwd())

# Ship the AI pre-configuration and bundled enemy sprites with the frozen app.
datas = [
    (str(ROOT / "config" / "ai_profile.toml"), "config"),
    (str(ROOT / "python" / "fps_coach" / "assets" / "enemies"), "fps_coach/assets/enemies"),
]

block_cipher = None

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT / "python")],
    binaries=[],
    datas=datas,
    # fps_coach.screen_recorder imports mss/cv2/numpy behind guards; mss picks its
    # platform backend at run time, so its submodules are collected explicitly.
    hiddenimports=collect_submodules("fps_coach") + collect_submodules("mss"),
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
