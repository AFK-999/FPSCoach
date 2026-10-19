"""Start the local Ollama service in the background at app startup.

Goal: the AI is ready to use immediately, without the user starting Ollama by
hand. This is best-effort and never raises - if Ollama isn't installed, the chat
page's one-click download button still guides the user.

Resolution order for the executable:
  1. A runtime bundled next to the frozen app (FULL build): ``<exe dir>\\ollama``
     plus its ``<exe dir>\\models`` store.
  2. A dev/staged copy under ``build/ai_assets/ollama``.
  3. ``ollama`` found on PATH (LITE build / user-installed).

Only a LOCAL endpoint (localhost / 127.0.0.1) is ever auto-started.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

DEFAULT_URL = os.environ.get("FPSCOACH_OLLAMA_URL", "http://127.0.0.1:11434")

# Set once we successfully launch, so we don't spawn duplicates within a session.
_started = False
_lock = threading.Lock()


def is_running(base_url: str = DEFAULT_URL, timeout: float = 1.0) -> bool:
    """True if an Ollama server answers at ``base_url``."""
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - local URL
            return 200 <= getattr(response, "status", 200) < 500
    except (URLError, OSError, ValueError):
        return False


def _is_local(base_url: str) -> bool:
    host = base_url.lower()
    return "127.0.0.1" in host or "localhost" in host or "[::1]" in host


def _exe_name() -> str:
    return "ollama.exe" if os.name == "nt" else "ollama"


def _bundled_base() -> Path | None:
    """Return a directory that contains an ``ollama/<exe>`` runtime, or None."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    # dev / staged fallback: project root (…/python/fps_coach/ -> root)
    candidates.append(Path(__file__).resolve().parents[2])
    for base in candidates:
        for sub in (base, base / "build" / "ai_assets"):
            if (sub / "ollama" / _exe_name()).exists():
                return sub
    return None


def find_executable() -> tuple[str, dict[str, str]] | None:
    """Return (exe_path, env_overrides) for the Ollama runtime, or None.

    ``env_overrides`` points OLLAMA_MODELS at a bundled model store when present.
    """
    base = _bundled_base()
    if base is not None:
        exe = base / "ollama" / _exe_name()
        env: dict[str, str] = {}
        models = base / "models"
        if models.exists():
            env["OLLAMA_MODELS"] = str(models)
        return str(exe), env
    path_exe = shutil.which("ollama")
    if path_exe:
        return path_exe, {}
    return None


def _spawn(exe: str, env_overrides: dict[str, str]) -> bool:
    env = {**os.environ, **env_overrides}
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if os.name == "nt":
        # Detached, no flashing console window.
        create_no_window = 0x08000000
        detached_process = 0x00000008
        kwargs["creationflags"] = create_no_window | detached_process
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([exe, "serve"], **kwargs)  # noqa: S603
        return True
    except (OSError, ValueError):
        return False


def ensure_running(base_url: str = DEFAULT_URL, wait_seconds: float = 0.0) -> bool:
    """Start the local Ollama server if it isn't already running.

    Returns True if the server is (or becomes) reachable. Never raises.
    With the default ``wait_seconds=0`` it returns immediately after launching,
    so startup is not blocked; the chat page detects readiness on its own.
    """
    global _started
    if not _is_local(base_url):
        return False
    if is_running(base_url):
        return True
    with _lock:
        if is_running(base_url):
            return True
        found = find_executable()
        if not found:
            return False
        if not _spawn(*found):
            return False
        _started = True
    if wait_seconds <= 0:
        return True
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_running(base_url):
            return True
        time.sleep(0.25)
    return is_running(base_url)


def ensure_running_async(base_url: str = DEFAULT_URL) -> None:
    """Launch the service from a daemon thread so app startup is never delayed."""
    threading.Thread(
        target=ensure_running, args=(base_url,), daemon=True, name="ollama-autostart"
    ).start()
