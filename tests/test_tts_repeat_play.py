"""Read-aloud keeps speaking: repeated Test voice clicks, and failed sentences are reported.

Bug: after the first sentence, later ones were silently lost until read-aloud was turned off and
on again. A failed run inside pyttsx3 left the engine refusing later runs, and the worker
swallowed the error. The fix gives each later sentence a fresh engine and reports failures.

These tests use a fake engine that behaves the same way (refuses a run after a failed one, no
audio). The real-driver regression (a second run hangs on pyttsx3's dummy driver) is covered in
tests/test_tts_engine_hang.py.
"""

from __future__ import annotations

import os
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from fps_coach import tts_service  # noqa: E402
from fps_coach.tts_service import InitResult, SpeechOptions, _Pyttsx3Speaker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _StickyEngine:
    """Works like pyttsx3: the loop flag stays set if a run raised."""

    spoken_log: list[str] = []  # shared across engines, so tests see every sentence

    def __init__(self, fail_next_run: bool = False) -> None:
        self._in_loop = False
        self._pending: list[str] = []
        self._fail_next_run = fail_next_run
        self.props: list[tuple[str, object]] = []
        self._voice = "id-default"

    def getProperty(self, name):
        if name == "voices":
            return [
                types.SimpleNamespace(id="id-zira", name="Zira"),
                types.SimpleNamespace(id="id-david", name="David"),
            ]
        if name == "voice":
            return self._voice
        return None

    def setProperty(self, name, value):
        self.props.append((name, value))

    def say(self, text: str) -> None:
        self._pending.append(text)

    def runAndWait(self) -> None:
        if self._in_loop:
            raise RuntimeError("run loop already started")
        self._in_loop = True
        if self._fail_next_run:
            self._fail_next_run = False
            raise OSError("driver hiccup")  # the flag is left set, as in pyttsx3
        _StickyEngine.spoken_log.extend(self._pending)
        self._pending = []
        self._in_loop = False

    def stop(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clear_log():
    _StickyEngine.spoken_log = []
    yield


def test_a_failed_run_leaves_one_engine_refusing_every_later_sentence() -> None:
    # Documents the failure mode from pyttsx3's engine.runAndWait: the flag stays set when a run
    # raises, so the same engine refuses every later sentence.
    engine = _StickyEngine(fail_next_run=True)
    with pytest.raises(OSError):
        engine.say("one")
        engine.runAndWait()
    engine.say("two")
    with pytest.raises(RuntimeError, match="run loop already started"):
        engine.runAndWait()


def test_every_later_sentence_gets_a_fresh_engine() -> None:
    made: list[_StickyEngine] = []

    def make():
        engine = _StickyEngine()
        made.append(engine)
        return engine

    speaker = _Pyttsx3Speaker(_StickyEngine(), None, make_engine=make)
    for text in ("one", "two", "three"):
        speaker.say(text)

    assert _StickyEngine.spoken_log == ["one", "two", "three"]
    assert len(made) == 2  # the first sentence used the start-up engine


def test_a_failed_sentence_does_not_stop_the_next_one() -> None:
    speaker = _Pyttsx3Speaker(
        _StickyEngine(fail_next_run=True), None, make_engine=_StickyEngine
    )
    with pytest.raises(OSError):
        speaker.say("lost")
    speaker.say("next")
    assert _StickyEngine.spoken_log == ["next"]


def test_settings_reach_every_new_engine() -> None:
    made: list[_StickyEngine] = []

    def make():
        engine = _StickyEngine()
        made.append(engine)
        return engine

    speaker = _Pyttsx3Speaker(_StickyEngine(), None, make_engine=make)
    speaker.voices()
    speaker.apply(SpeechOptions(speed=1.5, volume=40, voice="Zira"))
    speaker.say("one")
    speaker.say("two")

    for engine in made:
        assert ("rate", 300) in engine.props
        assert ("volume", 0.4) in engine.props
        assert ("voice", "id-zira") in engine.props


def _wait_until(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _install_service_engine(monkeypatch, speaker) -> None:
    monkeypatch.setattr(tts_service, "ensure_dependency", lambda progress=None, timeout=600.0: True)
    monkeypatch.setattr(
        tts_service, "init_engine", lambda platform=None, progress=None: InitResult(speaker=speaker)
    )


def test_test_voice_plays_every_time_through_the_speaker_thread(qapp, monkeypatch) -> None:
    from fps_coach.ui.threads import _TtsService

    phrase = "This is the coach voice. Aim a little higher on the second target."
    speaker = _Pyttsx3Speaker(_StickyEngine(), None, make_engine=_StickyEngine)
    _install_service_engine(monkeypatch, speaker)

    service = _TtsService(options=SpeechOptions())
    service.start()
    try:
        for count in range(1, 4):
            service.enqueue(phrase)
            assert _wait_until(lambda: _StickyEngine.spoken_log.count(phrase) == count), (
                f"Test voice play {count} was not spoken"
            )
    finally:
        service.stop()
        service.wait(3000)


def test_a_failed_sentence_is_reported_in_status(qapp, monkeypatch) -> None:
    from fps_coach.ui.threads import _TtsService

    speaker = _Pyttsx3Speaker(
        _StickyEngine(fail_next_run=True), None, make_engine=_StickyEngine
    )
    _install_service_engine(monkeypatch, speaker)

    messages: list[str] = []
    service = _TtsService(options=SpeechOptions())
    service.status.connect(messages.append)
    service.start()
    try:
        service.enqueue("first reply")
        assert _wait_until(
            lambda: (qapp.processEvents() or True) and any("Could not read" in m for m in messages)
        )
        service.enqueue("second reply")
        # speakable_text adds a full stop, so match the start of the sentence.
        assert _wait_until(
            lambda: (qapp.processEvents() or True)
            and any(text.startswith("second reply") for text in _StickyEngine.spoken_log)
        )
    finally:
        service.stop()
        service.wait(3000)
    assert any("driver hiccup" in m for m in messages)
