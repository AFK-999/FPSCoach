"""Text-to-speech for AI responses via pyttsx3 (offline, cross-platform).

``pyttsx3`` is an optional dependency that is auto-installed on demand (like the
voice-input stack), so the user never has to install it by hand. This module is
the dependency/engine layer; the UI owns a small worker thread that consumes a
queue of texts and speaks them.

Degrades gracefully (clear message / False) where the package or an audio output
device is unavailable, and stays importable/unit-testable headless.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys

REQUIRED_PACKAGE = "pyttsx3"


def dependency_available() -> bool:
    return importlib.util.find_spec(REQUIRED_PACKAGE) is not None


def ensure_dependency(progress=None, timeout: float = 600.0) -> bool:
    """Ensure pyttsx3 is installed, pip-installing it if missing. Returns True
    when it is importable. Best-effort, non-raising."""
    if dependency_available():
        return True
    if progress:
        progress("Installing text-to-speech (pyttsx3)... this runs once.")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", REQUIRED_PACKAGE],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        if progress:
            progress(f"Text-to-speech install failed: {exc}")
        return False
    importlib.invalidate_caches()
    ok = dependency_available()
    if progress:
        progress("Text-to-speech ready." if ok else "Text-to-speech still unavailable.")
    return ok


def create_engine():
    """Create and return a pyttsx3 engine, or None if it cannot be initialized."""
    if not dependency_available():
        return None
    try:
        import pyttsx3

        return pyttsx3.init()
    except Exception:
        return None
