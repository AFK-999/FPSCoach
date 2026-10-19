"""Optional voice input for the AI chat (speech-to-text).

Lets the user dictate a message to the coach by voice instead of typing. This is
intentionally a small, optional layer:

* It uses an optional speech-recognition backend if one is installed
  (``speech_recognition`` or ``vosk``) and degrades gracefully - with a clear
  install hint - when none is present. No heavy dependency is forced on the app.
* Recognition is a one-shot "listen for a phrase" (push-to-talk style) triggered
  from the in-app mic button. We deliberately do NOT install global/system-wide
  keyboard hooks for in-game push-to-talk, to stay within the app's no-injection,
  anti-cheat-friendly boundary.

Backend detection uses importlib (no import side effects) so this module stays
importable and unit-testable even where no audio stack exists.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

_INSTALL_HINT = (
    "Voice input needs an optional package. Install one of:\n"
    "    pip install SpeechRecognition PyAudio\n"
    "  or set up Vosk for fully offline recognition."
)


@dataclass(slots=True)
class VoiceResult:
    ok: bool
    text: str = ""
    message: str = ""


def available_backend() -> str | None:
    """Return the name of an installed STT backend, or None."""
    if importlib.util.find_spec("speech_recognition") is not None:
        return "speech_recognition"
    if importlib.util.find_spec("vosk") is not None:
        return "vosk"
    return None


def is_available() -> bool:
    return available_backend() is not None


def recognize_once(timeout: float = 6.0, phrase_limit: float = 12.0) -> VoiceResult:
    """Capture a single spoken phrase and return it as text.

    Never raises - returns ``VoiceResult(ok=False, message=...)`` on any problem
    (no backend, no microphone, nothing heard, recognition error).
    """
    backend = available_backend()
    if backend is None:
        return VoiceResult(False, message=_INSTALL_HINT)
    if backend == "speech_recognition":
        return _recognize_speechrecognition(timeout, phrase_limit)
    # Vosk offline path: validate/configure the model, then recognize one phrase.
    from fps_coach import vosk_service

    ok, text, message = vosk_service.recognize_once(phrase_limit=phrase_limit)
    return VoiceResult(ok, text=text, message=message)


def _recognize_speechrecognition(timeout: float, phrase_limit: float) -> VoiceResult:
    try:
        import speech_recognition as sr  # noqa: WPS433
    except Exception:  # pragma: no cover - import guarded by find_spec
        return VoiceResult(False, message=_INSTALL_HINT)
    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:  # needs PyAudio + a mic
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
    except Exception:
        return VoiceResult(False, message="No microphone was available or nothing was heard.")
    try:
        text = recognizer.recognize_google(audio)  # online; offline needs Vosk/Sphinx
    except Exception:
        return VoiceResult(False, message="Could not understand the audio. Please try again.")
    text = (text or "").strip()
    if not text:
        return VoiceResult(False, message="Nothing was recognized.")
    return VoiceResult(True, text=text)
