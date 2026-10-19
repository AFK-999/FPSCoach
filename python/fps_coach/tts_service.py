"""Text-to-speech for AI responses.

Engines are tried in this order until one initializes:

1. pyttsx3 with the platform's default driver (SAPI5 on Windows, NSSS on macOS,
   eSpeak on Linux).
2. pyttsx3 with any other driver that exists for this OS (macOS: AVSpeech).
3. Windows only: PowerShell's ``System.Speech`` synthesizer, which needs no pip
   packages. pyttsx3 ships only the SAPI5 driver on Windows, so this is the only
   real alternative there.

Every failed attempt is recorded in :class:`InitResult` so the UI can say exactly
what was tried. Nothing in this module raises on a missing or broken speech
stack; it degrades to a clear message instead.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

REQUIRED_PACKAGE = "pyttsx3"

# Platform-specific packages pyttsx3 needs at runtime: (pip name, importable module).
# On Windows pyttsx3 normally pulls these in, but they can be missing if pyttsx3
# was installed with --no-deps or a pywin32 install failed. We check and repair.
PLATFORM_PACKAGES: dict[str, tuple[tuple[str, str], ...]] = {
    "win32": (("pywin32", "pythoncom"), ("comtypes", "comtypes")),
}

# pyttsx3 drivers per platform. The first entry is pyttsx3's own default there.
PYTTSX3_DRIVERS: dict[str, tuple[str, ...]] = {
    "win32": ("sapi5",),
    "darwin": ("nsss", "avspeech"),
    "linux": ("espeak",),
}

POWERSHELL_TEXT_ENV = "FPSCOACH_TTS_TEXT"
POWERSHELL_RATE_ENV = "FPSCOACH_TTS_RATE"
POWERSHELL_VOLUME_ENV = "FPSCOACH_TTS_VOLUME"
POWERSHELL_VOICE_ENV = "FPSCOACH_TTS_VOICE"
# Exit code 3 means "System.Speech works but no voices are installed".
POWERSHELL_PROBE = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$n = $s.GetInstalledVoices().Count; $s.Dispose(); "
    "if ($n -eq 0) { exit 3 }"
)
POWERSHELL_SPEAK = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    f"$s.Rate = [int]$env:{POWERSHELL_RATE_ENV}; "
    f"$s.Volume = [int]$env:{POWERSHELL_VOLUME_ENV}; "
    f"if ($env:{POWERSHELL_VOICE_ENV}) {{ try {{ $s.SelectVoice($env:{POWERSHELL_VOICE_ENV}) }} catch {{ }} }}; "
    f"$s.Speak($env:{POWERSHELL_TEXT_ENV}); $s.Dispose()"
)
POWERSHELL_VOICES = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }; $s.Dispose()"
)

# --- speech options (rate, volume, voice) -----------------------------------
# Pitch is not offered: neither pyttsx3 nor System.Speech exposes a pitch setting.

SPEED_MIN, SPEED_MAX = 0.5, 2.0  # multiples of normal speaking rate
VOLUME_MIN, VOLUME_MAX = 0, 100  # percent
PYTTSX3_BASE_RATE = 200  # words per minute at 1.0x


@dataclass(frozen=True)
class SpeechOptions:
    """What the speech engine should sound like. Applied before each utterance."""

    speed: float = 1.0
    volume: int = 100
    voice: str = ""  # a voice name from the engine; "" means the engine's default

    def clamped(self) -> "SpeechOptions":
        return SpeechOptions(
            speed=min(SPEED_MAX, max(SPEED_MIN, float(self.speed))),
            volume=int(min(VOLUME_MAX, max(VOLUME_MIN, round(float(self.volume))))),
            voice=str(self.voice or ""),
        )


def pyttsx3_rate(speed: float) -> int:
    """pyttsx3 rate is words per minute."""
    return int(round(PYTTSX3_BASE_RATE * speed))


def powershell_rate(speed: float) -> int:
    """System.Speech rate runs from -10 (slowest) to 10. 1.0x is 0. The mapping is approximate."""
    return int(max(-10, min(10, round((speed - 1.0) * 10))))

_HINTS = {
    "win32": (
        "Fix: check that Windows has a speech voice installed, then turn the option "
        "back on. If pywin32 or comtypes keep failing, run: "
        "python -m pip install pywin32 comtypes"
    ),
    "darwin": "Fix: check System Settings > Accessibility > Spoken Content for an installed voice.",
    "linux": "Fix: install eSpeak NG (for example: sudo apt install espeak-ng), then turn the option back on.",
}

Progress = Callable[[str], None] | None


class Speaker(Protocol):
    name: str

    def voices(self) -> list[str]: ...

    def apply(self, options: SpeechOptions) -> None: ...

    def say(self, text: str) -> None: ...

    def close(self) -> None: ...


@dataclass
class InitResult:
    speaker: Speaker | None = None
    # One line per failed attempt, in the order they were tried.
    attempts: list[str] = field(default_factory=list)


class _Pyttsx3Speaker:
    def __init__(self, engine, driver: str | None) -> None:
        self._engine = engine
        self.name = f"pyttsx3 {driver or 'default'} driver"
        self._voice_ids: dict[str, str] = {}
        self._default_voice_id = None
        self._applied: SpeechOptions | None = None

    def voices(self) -> list[str]:
        """Voice names this engine can use. Also remembers the engine's default voice."""
        names: list[str] = []
        self._voice_ids = {}
        try:
            self._default_voice_id = self._engine.getProperty("voice")
        except Exception:  # noqa: BLE001 - not every driver reports a default
            self._default_voice_id = None
        for voice in self._engine.getProperty("voices") or []:
            voice_id = str(getattr(voice, "id", "") or "")
            name = str(getattr(voice, "name", "") or voice_id)
            if name and voice_id:
                names.append(name)
                self._voice_ids[name] = voice_id
        return names

    def apply(self, options: SpeechOptions) -> None:
        """Set rate, volume, and voice on this thread, before the next utterance."""
        if options == self._applied:
            return
        self._engine.setProperty("rate", pyttsx3_rate(options.speed))
        self._engine.setProperty("volume", options.volume / 100.0)
        if options.voice:
            voice_id = self._voice_ids.get(options.voice)
            if voice_id is not None:
                self._engine.setProperty("voice", voice_id)
        elif self._default_voice_id is not None:
            self._engine.setProperty("voice", self._default_voice_id)
        self._applied = options

    def say(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()

    def close(self) -> None:
        try:
            self._engine.stop()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            pass


class _PowerShellSpeaker:
    name = "PowerShell System.Speech"

    def __init__(self, exe: str) -> None:
        self._exe = exe
        self._options = SpeechOptions()

    def voices(self) -> list[str]:
        try:
            probe = subprocess.run(
                [self._exe, "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_VOICES],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:  # noqa: BLE001 - voice list is optional
            return []
        return [line.strip() for line in probe.stdout.splitlines() if line.strip()]

    def apply(self, options: SpeechOptions) -> None:
        self._options = options.clamped()

    def say(self, text: str) -> None:
        # Text goes through an environment variable, so quotes or '$' in the
        # reply cannot break or inject into the PowerShell command.
        opts = self._options
        env = {
            **os.environ,
            POWERSHELL_TEXT_ENV: text,
            POWERSHELL_RATE_ENV: str(powershell_rate(opts.speed)),
            POWERSHELL_VOLUME_ENV: str(opts.volume),
            POWERSHELL_VOICE_ENV: opts.voice,
        }
        subprocess.run(
            [self._exe, "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SPEAK],
            env=env,
            check=True,
            capture_output=True,
            timeout=300,
        )

    def close(self) -> None:
        pass


def _last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _describe(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


# --- dependencies -----------------------------------------------------------


def dependency_available() -> bool:
    return importlib.util.find_spec(REQUIRED_PACKAGE) is not None


def _module_missing(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is None
    except (ImportError, ValueError):
        return True


def missing_platform_packages(platform: str | None = None) -> list[str]:
    """Pip names of this platform's required packages that cannot be imported."""
    plat = platform or sys.platform
    return [pip for pip, module in PLATFORM_PACKAGES.get(plat, ()) if _module_missing(module)]


def _pip_install(packages: list[str], progress: Progress, timeout: float) -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *packages],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        if progress:
            detail = _last_line(exc.stderr) or f"pip exited with code {exc.returncode}"
            progress(f"Installing {', '.join(packages)} failed: {detail}")
        return False
    except Exception as exc:  # noqa: BLE001 - install is best-effort
        if progress:
            progress(f"Installing {', '.join(packages)} failed: {_describe(exc)}")
        return False
    return True


def ensure_platform_dependencies(
    progress: Progress = None, timeout: float = 600.0, platform: str | None = None
) -> list[str]:
    """Install any missing platform packages. Returns the ones still missing."""
    missing = missing_platform_packages(platform)
    if not missing:
        return []
    if progress:
        progress(f"Installing speech support ({', '.join(missing)})... this runs once.")
    _pip_install(missing, progress, timeout)
    importlib.invalidate_caches()
    return missing_platform_packages(platform)


def ensure_dependency(progress: Progress = None, timeout: float = 600.0) -> bool:
    """Make pyttsx3 and the platform packages importable, installing what's missing.

    Returns True only when everything is present. Best-effort and non-raising. The
    worker does not stop on False: :func:`init_engine` still tries the fallbacks.
    """
    ok = True
    if not dependency_available():
        if progress:
            progress("Installing text-to-speech (pyttsx3)... this runs once.")
        ok = _pip_install([REQUIRED_PACKAGE], progress, timeout)
        importlib.invalidate_caches()
        ok = ok and dependency_available()
    still_missing = ensure_platform_dependencies(progress, timeout)
    ready = ok and not still_missing
    if progress:
        progress(
            "Text-to-speech packages ready."
            if ready
            else "Some text-to-speech packages are missing; trying fallbacks."
        )
    return ready


# --- COM (Windows) ----------------------------------------------------------


@contextmanager
def com_apartment() -> Iterator[None]:
    """Initialize COM on the calling thread for the duration of the block.

    SAPI5 needs COM on the thread that uses it. Qt worker threads don't get that
    automatically. No-op on non-Windows platforms or when pywin32 is missing.
    """
    initialized = False
    pythoncom = None
    if sys.platform == "win32":
        try:
            import pythoncom as _pythoncom

            _pythoncom.CoInitialize()
            pythoncom = _pythoncom
            initialized = True
        except Exception:  # noqa: BLE001 - fall through; SAPI5 may still work
            initialized = False
    try:
        yield
    finally:
        if initialized and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


# --- engine selection -------------------------------------------------------


def _pyttsx3_driver_chain(platform: str) -> list[str | None]:
    """None means pyttsx3's default driver; then any other driver for this OS."""
    names = PYTTSX3_DRIVERS.get(platform, ())
    return [None, *names[1:]]


def _speaker_from_pyttsx3(driver: str | None) -> Speaker:
    import pyttsx3  # lazy: keeps the module importable without the package

    engine = pyttsx3.init(driverName=driver)
    voices = engine.getProperty("voices")
    if not voices:
        raise RuntimeError("no voices are installed for this driver")
    return _Pyttsx3Speaker(engine, driver)


def _init_powershell() -> Speaker:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        raise RuntimeError("powershell was not found on PATH")
    probe = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_PROBE],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode == 3:
        raise RuntimeError("no speech voices are installed in Windows")
    if probe.returncode != 0:
        raise RuntimeError(
            _last_line(probe.stderr) or f"System.Speech probe exited with code {probe.returncode}"
        )
    return _PowerShellSpeaker(exe)


def init_engine(platform: str | None = None, progress: Progress = None) -> InitResult:
    """Try each engine in order and return the first that initializes.

    Call this on the thread that will speak (inside :func:`com_apartment` on
    Windows). It never raises. When every option fails, ``speaker`` is None and
    ``attempts`` says why each one failed.
    """
    plat = platform or sys.platform
    result = InitResult()

    attempts: list[tuple[str, Callable[[], Speaker]]] = []
    if dependency_available():
        for driver in _pyttsx3_driver_chain(plat):
            attempts.append(
                (
                    f"pyttsx3 {driver or 'default'} driver",
                    lambda d=driver: _speaker_from_pyttsx3(d),
                )
            )
    else:
        result.attempts.append("pyttsx3 is not installed")
    if plat == "win32":
        attempts.append(("PowerShell System.Speech", _init_powershell))

    for index, (label, make) in enumerate(attempts):
        try:
            result.speaker = make()
            return result
        except Exception as exc:  # noqa: BLE001 - every failure becomes a message
            result.attempts.append(f"{label}: {_describe(exc)}")
            if progress and index + 1 < len(attempts):
                progress(f"{label} failed; trying the next option...")
    return result


# --- text cleaning ----------------------------------------------------------

# Symbols with a spoken form. Other symbols are dropped rather than read out.
_SPOKEN_SYMBOLS = {
    "%": " percent ",
    "&": " and ",
    "+": " plus ",
    "\u00d7": " times ",
    "\u00f7": " divided by ",
    "=": " equals ",
    "/": " ",
    "_": " ",
}
# Punctuation that helps the speech engine pause and intonate. Everything else is dropped.
_KEPT_PUNCTUATION = set(".,;:!?'\"()-\u2013\u2014\u2026")


def speakable_text(text: str | None) -> str:
    """Turn an AI reply into text a speech engine can read aloud cleanly.

    Removes markdown (headings, bold, bullets, links, inline code), replaces fenced code
    with a short note, speaks a few symbols as words (% becomes "percent"), and drops
    other symbols such as emoji, arrows, and stray markup characters, which speech
    engines otherwise read out by name. The text shown in the transcript is not changed.
    Returns "" when nothing speakable is left.
    """
    if not text:
        return ""
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"```.*?```", " (code example omitted) ", out, flags=re.DOTALL)
    out = re.sub(r"`([^`\n]*)`", r"\1", out)
    out = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", out)
    out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", out)
    out = re.sub(r"https?://\S+", "link", out)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", out)
    out = re.sub(r"(?m)^\s*(?:[-*+\u2022]|\u25e6)\s+", "", out)
    out = re.sub(r"(\*\*|__)(.+?)\1", r"\2", out)
    out = re.sub(r"(?m)^\s*[-=_*|]{3,}\s*$", "", out)
    out = re.sub(r"\s+-\s+", ", ", out)
    out = out.replace("|", ", ")

    # One sentence per line: a line break becomes a full stop so the engine pauses.
    lines = []
    for raw in out.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line[-1] not in ".!?:;,":
            line += "."
        lines.append(line)
    out = " ".join(lines)

    for symbol, spoken in _SPOKEN_SYMBOLS.items():
        out = out.replace(symbol, spoken)

    # Allow-list: letters, digits, spaces, combining accents, and the pause punctuation.
    # Every other character (markup such as * # { } [ ] < > ^ \ ~, symbols, emoji,
    # control characters) becomes a space, so no engine reads it out by name.
    kept = []
    for ch in out:
        if ch.isalnum() or ch.isspace() or ch in _KEPT_PUNCTUATION:
            kept.append(ch)
        elif unicodedata.category(ch).startswith("M"):
            kept.append(ch)
        else:
            kept.append(" ")
    out = "".join(kept)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"([.,;:!?])(?:\s*[.,;:!?])+", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    if not any(ch.isalnum() for ch in out):
        return ""
    return out


def format_unavailable(result: InitResult, platform: str | None = None) -> str:
    """Human-readable explanation for the chat when no speech engine works."""
    plat = platform or sys.platform
    lines = ["Text-to-speech is unavailable on this system, so replies will show as text only."]
    if result.attempts:
        lines.append("What was tried:")
        lines.extend(f"  - {attempt}" for attempt in result.attempts)
    hint = _HINTS.get(plat)
    if hint:
        lines.append(hint)
    return "\n".join(lines)
