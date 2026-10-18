"""Automatic environment self-repair for FPS Coach.

The app installs itself as a pip *editable* install that records the project's
absolute path. When the whole project folder is MOVED or RENAMED, that recorded
path goes stale and imports/launches can break (this is what
``Repair-FPSCoach-Path-and-Run.bat`` fixes manually).

:func:`ensure_environment` performs the same check automatically at startup. It
is deliberately lightweight: in the normal case (nothing moved) it does nothing
and returns immediately. It only reinstalls the editable package when it detects
the project has actually moved. It never raises - a failed repair just returns
False so the caller can decide what to do.

Handles paths containing spaces and non-ASCII characters (uses ``sys.executable``
and pathlib throughout; no shell string building).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MARKER_NAME = ".fpscoach_env.json"


def project_root() -> Path:
    """Project root = two levels up from python/fps_coach/env_repair.py."""
    return Path(__file__).resolve().parents[2]


def _marker_path() -> Path:
    return project_root() / MARKER_NAME


def _recorded_root() -> str | None:
    marker = _marker_path()
    if not marker.exists():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("root")
    except Exception:
        return None


def _record_root() -> None:
    try:
        _marker_path().write_text(
            json.dumps({"root": str(project_root())}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def package_location_is_stale() -> bool:
    """True if the imported ``fps_coach`` package resolves somewhere other than
    this project's ``python`` directory (e.g. a lingering old copy after a move).
    """
    try:
        import fps_coach

        imported_from = Path(fps_coach.__file__).resolve().parents[1]
        expected = (project_root() / "python").resolve()
        return imported_from != expected
    except Exception:
        return True


def needs_repair() -> bool:
    recorded = _recorded_root()
    moved = recorded is not None and Path(recorded) != project_root()
    return moved or package_location_is_stale()


def repair(python_exe: str | None = None, verbose: bool = True) -> bool:
    """Re-point the editable install at the current project folder.

    Mirrors Repair-FPSCoach-Path-and-Run.bat: ``pip install --editable . --no-deps``.
    Returns True on success.
    """
    root = project_root()
    exe = python_exe or sys.executable
    if verbose:
        print(f"[fps_coach] Project folder changed - repairing editable install for: {root}")
    try:
        subprocess.run(
            [exe, "-m", "pip", "install", "--editable", str(root), "--no-deps"],
            check=True,
            cwd=str(root),
        )
    except Exception as exc:  # noqa: BLE001 - never let repair crash the app
        if verbose:
            print(f"[fps_coach] Automatic repair failed: {exc}", file=sys.stderr)
            print("[fps_coach] Run 'Setup and Run.bat' for a full rebuild.", file=sys.stderr)
        return False
    _record_root()
    if verbose:
        print("[fps_coach] Repair complete.")
    return True


def ensure_environment(verbose: bool = True) -> bool:
    """Check the local environment and auto-repair if the folder moved/renamed.

    Call this once at startup, before loading heavy modules/UI. Fast no-op when
    nothing has changed. Never raises.
    """
    try:
        if needs_repair():
            ok = repair(verbose=verbose)
            if ok:
                _record_root()
            return ok
        _record_root()
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(0 if ensure_environment() else 1)
