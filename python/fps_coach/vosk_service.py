"""Vosk offline speech recognition: model management + background listening.

* :func:`dependencies_available` - True only when ``vosk`` and an audio input
  backend (``sounddevice``) are importable.
* :func:`find_model` / :func:`ensure_model` - locate a cached Vosk model or
  download+extract the small English model from the official Vosk site on first
  use, into a local cache folder.
* :func:`listen` - a blocking loop that streams microphone audio into a Vosk
  recognizer and calls back with each finalized phrase; stops when ``should_stop``
  returns True.

Everything degrades gracefully (clear exception / False) where the optional
packages, a microphone, or network are unavailable, so the module stays
importable and unit-testable in a headless environment.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from urllib.request import urlopen

# Packages required for offline voice: (pip name, import name).
REQUIRED_PACKAGES = (("vosk", "vosk"), ("sounddevice", "sounddevice"))

# Small (~40 MB) English model - good default for offline commands/dictation.
DEFAULT_MODEL_NAME = "vosk-model-small-en-us-0.15"
DEFAULT_MODEL_URL = f"https://alphacephei.com/vosk/models/{DEFAULT_MODEL_NAME}.zip"
SAMPLE_RATE = 16000


class VoskUnavailable(RuntimeError):
    """Raised when Vosk recognition cannot be set up."""


def cache_dir() -> Path:
    """Folder that holds downloaded Vosk models (project root / .fpscoach_vosk)."""
    return Path(__file__).resolve().parents[2] / ".fpscoach_vosk"


def dependencies_available() -> bool:
    return all(importlib.util.find_spec(mod) is not None for _pip, mod in REQUIRED_PACKAGES)


def missing_packages() -> list[str]:
    """pip names of the required packages that are not importable."""
    return [pip for pip, mod in REQUIRED_PACKAGES if importlib.util.find_spec(mod) is None]


def ensure_dependencies(progress=None, timeout: float = 900.0) -> bool:
    """Make sure vosk + sounddevice are installed, pip-installing them into the
    current interpreter if missing. Returns True when all deps are importable.

    Best-effort and non-raising: on any failure (no network, no pip, blocked) it
    reports via ``progress`` and returns False so the caller can guide the user.
    """
    if dependencies_available():
        return True
    missing = missing_packages()
    if not missing:
        return True
    if progress:
        progress(f"Installing voice packages ({', '.join(missing)})... this runs once.")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - surface any install problem gracefully
        if progress:
            progress(f"Automatic install failed: {exc}")
        return False
    importlib.invalidate_caches()
    ok = dependencies_available()
    if progress:
        progress("Voice packages ready." if ok else "Voice packages still unavailable after install.")
    return ok


def _model_path_file() -> Path:
    return cache_dir() / "model_path.txt"


def validate_model(path) -> bool:
    """True if ``path`` looks like a real, complete Vosk model directory.

    A Vosk model contains a ``conf`` folder plus an acoustic model (``am``) or a
    pre-built ``graph``. This guards against half-extracted / wrong folders.
    """
    try:
        p = Path(path)
    except TypeError:
        return False
    if not p.is_dir():
        return False
    children = {c.name for c in p.iterdir()}
    return "conf" in children and ("am" in children or "graph" in children)


def find_model() -> Path | None:
    """Return the path to an already-downloaded, VALID model, or None."""
    root = cache_dir()
    direct = root / DEFAULT_MODEL_NAME
    if validate_model(direct):
        return direct
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.is_dir() and validate_model(child):
                return child
    return None


def configured_model_path() -> Path | None:
    """Return the explicitly configured model path if it is still valid, else None."""
    marker = _model_path_file()
    if marker.exists():
        try:
            candidate = Path(marker.read_text(encoding="utf-8").strip())
            if validate_model(candidate):
                return candidate
        except OSError:
            return None
    return None


def set_configured_model_path(path) -> None:
    """Persist the exact, validated model path so startup can reuse it."""
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        _model_path_file().write_text(str(Path(path).resolve()), encoding="utf-8")
    except OSError:
        pass


def verify_and_configure(progress=None) -> Path | None:
    """Validate + lock in the model path WITHOUT downloading. Returns the path or
    None if no valid model is present yet. Safe to run on every startup.

    If a previously configured path is now wrong/missing, it is corrected to the
    actual on-disk model; if none exists, returns None so the caller can download.
    """
    configured = configured_model_path()
    if configured is not None:
        return configured
    found = find_model()
    if found is not None:
        set_configured_model_path(found)
        if progress:
            progress("Vosk model configured.")
        return found
    return None


def ensure_model(progress=None) -> Path:
    """Return a usable model path, downloading + extracting it if needed.

    ``progress`` is an optional callable receiving human-readable status strings.
    Raises :class:`VoskUnavailable` on any failure.
    """
    existing = find_model()
    if existing is not None:
        return existing

    root = cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / f"{DEFAULT_MODEL_NAME}.zip"
    try:
        if progress:
            progress(f"Downloading Vosk model {DEFAULT_MODEL_NAME} (~40 MB)...")
        with urlopen(DEFAULT_MODEL_URL) as response, open(zip_path, "wb") as out:  # noqa: S310
            out.write(response.read())
        if progress:
            progress("Extracting model...")
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(root)
    except Exception as exc:  # noqa: BLE001
        raise VoskUnavailable(f"Could not download/extract the Vosk model: {exc}") from exc
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass

    model = find_model()
    if model is None or not validate_model(model):
        raise VoskUnavailable(
            "The Vosk model was downloaded but the folder is incomplete or invalid."
        )
    # Lock in the exact, validated path so the app recognizes it next time.
    set_configured_model_path(model)
    return model


# One loaded Vosk model is kept for reuse. Loading a big model takes seconds and
# several gigabytes, so it must not happen on every phrase. Only one model is held at a
# time: switching to another path drops the old one first.
_MODEL_LOCK = threading.Lock()
_loaded_model: tuple[str, object] | None = None


def load_model(model_path):
    """Return the Vosk model at ``model_path``, loading it only on first use.

    Raises whatever ``vosk.Model`` raises. Callers wrap it in their own error handling.
    """
    global _loaded_model
    import vosk

    key = str(Path(model_path).resolve())
    with _MODEL_LOCK:
        if _loaded_model is not None and _loaded_model[0] == key:
            return _loaded_model[1]
        _loaded_model = None  # free the previous model before loading the next one
        model = vosk.Model(key)
        _loaded_model = (key, model)
        return model


def release_model() -> None:
    """Drop the cached model so its memory can be reclaimed (for example, voice turned off)."""
    global _loaded_model
    with _MODEL_LOCK:
        _loaded_model = None


def recognize_once(phrase_limit: float = 6.0, progress=None) -> tuple[bool, str, str]:
    """One-shot offline recognition with Vosk -> (ok, text, message).

    Resolves/validates the model path first (configuring it if needed, downloading
    only as a last resort), then captures a single phrase. Never raises.
    """
    if not dependencies_available():
        return (False, "", "Offline voice needs: pip install vosk sounddevice")
    try:
        model_path = verify_and_configure(progress) or ensure_model(progress)
    except VoskUnavailable as exc:
        return (False, "", str(exc))
    try:
        import json
        import queue
        import time as _time

        import sounddevice as sd
        import vosk

        model = load_model(model_path)
        recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE)
        audio_q: queue.Queue[bytes] = queue.Queue()

        def _cb(indata, _frames, _t, _status):  # pragma: no cover - audio cb
            audio_q.put(bytes(indata))

        deadline = _time.time() + max(2.0, phrase_limit)
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000, dtype="int16",
                               channels=1, callback=_cb):
            while _time.time() < deadline:
                try:
                    data = audio_q.get(timeout=0.3)
                except Exception:
                    continue
                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                    if text:
                        return (True, text, "")
        text = json.loads(recognizer.FinalResult()).get("text", "").strip()
        if text:
            return (True, text, "")
        return (False, "", "Nothing was recognized. Please try again.")
    except Exception as exc:  # noqa: BLE001
        return (False, "", f"Voice capture failed: {exc}")


def listen(model_path, on_text, should_stop, on_status=None) -> None:
    """Stream microphone audio into Vosk and call ``on_text(phrase)`` per result.

    Blocking - run on a background thread. ``should_stop()`` is polled to exit.
    Raises :class:`VoskUnavailable` if the stack isn't ready.
    """
    if not dependencies_available():
        raise VoskUnavailable(
            "Offline voice needs optional packages: pip install vosk sounddevice"
        )
    try:
        import queue

        import sounddevice as sd
        import vosk
    except Exception as exc:  # noqa: BLE001
        raise VoskUnavailable(f"Vosk/sounddevice import failed: {exc}") from exc

    try:
        model = load_model(model_path)
        recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    except Exception as exc:  # noqa: BLE001
        raise VoskUnavailable(f"Could not load the Vosk model: {exc}") from exc

    audio_q: queue.Queue[bytes] = queue.Queue()

    def _callback(indata, _frames, _time, _status):  # pragma: no cover - audio cb
        audio_q.put(bytes(indata))

    if on_status:
        on_status("Listening for voice commands...")
    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE, blocksize=8000, dtype="int16",
            channels=1, callback=_callback,
        ):
            while not should_stop():
                try:
                    data = audio_q.get(timeout=0.3)
                except Exception:
                    continue
                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                    if text:
                        on_text(text)
    except Exception as exc:  # noqa: BLE001
        raise VoskUnavailable(f"Audio capture failed: {exc}") from exc
