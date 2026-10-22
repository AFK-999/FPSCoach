"""Test voice must play every time: no shared engine state, and a watchdog for a stalled engine.

Found with pyttsx3's own "dummy" driver (no audio, same engine code path): pyttsx3.init() returns
one cached engine per driver name, and the second sentence on it never returns from runAndWait().
The worker thread then stops, so nothing else is spoken until read-aloud is toggled. These tests
use the real dummy driver when pyttsx3 is installed.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from fps_coach import tts_service  # noqa: E402
from fps_coach.tts_service import InitResult, SpeechOptions, _Pyttsx3Speaker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait_until(predicate, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


# -- the engine is not shared ---------------------------------------------------------

def test_each_new_engine_is_a_separate_object() -> None:
    pytest.importorskip("pyttsx3")
    first = tts_service._new_engine("dummy")
    second = tts_service._new_engine("dummy")
    assert first is not second


def test_new_engines_speak_every_sentence_on_the_dummy_driver() -> None:
    pytest.importorskip("pyttsx3")
    speaker = _Pyttsx3Speaker(
        tts_service._new_engine("dummy"), "dummy", make_engine=lambda: tts_service._new_engine("dummy")
    )
    finished: list[str] = []
    errors: list[BaseException] = []

    def speak_all() -> None:
        try:
            for index in range(4):
                speaker.say(f"sentence number {index}")
                finished.append(f"sentence number {index}")
        except BaseException as exc:  # noqa: BLE001 - reported to the test below
            errors.append(exc)

    worker = threading.Thread(target=speak_all, daemon=True)
    worker.start()
    worker.join(60)
    assert not worker.is_alive(), "the speaker stopped responding"
    assert errors == []
    assert len(finished) == 4


def test_test_voice_plays_every_time_with_the_real_dummy_driver(qapp, monkeypatch) -> None:
    pytest.importorskip("pyttsx3")
    from fps_coach.ui.threads import _TtsService

    speaker = _Pyttsx3Speaker(
        tts_service._new_engine("dummy"), "dummy", make_engine=lambda: tts_service._new_engine("dummy")
    )
    monkeypatch.setattr(tts_service, "ensure_dependency", lambda progress=None, timeout=600.0: True)
    monkeypatch.setattr(
        tts_service, "init_engine", lambda platform=None, progress=None: InitResult(speaker=speaker)
    )
    finished: list[int] = []
    service = _TtsService(options=SpeechOptions())
    service.utterance_finished.connect(lambda: finished.append(1))
    service.start()
    try:
        for count in range(1, 4):
            service.enqueue("This is the coach voice. Aim a little higher on the second target.")
            # Qt delivers the worker's signals on the main thread, so the events must be processed.
            assert _wait_until(
                lambda: (qapp.processEvents() or True) and len(finished) >= count
            ), f"test voice play {count} did not finish"
    finally:
        service.stop()
        service.wait(5000)


# -- the watchdog ----------------------------------------------------------------------

class _StubService:
    def __init__(self) -> None:
        self.abandoned = False
        self.texts: list[str] = []

    def abandon(self) -> None:
        self.abandoned = True

    def stop(self) -> None:
        pass

    def wait(self, _ms: int = 0) -> bool:
        return True

    def enqueue(self, text: str) -> None:
        self.texts.append(text)


@pytest.fixture()
def window(qapp, monkeypatch):
    from fps_coach.ui.main_window import MainWindow
    from fps_coach.ui.widgets import _settings

    store = _settings()
    before = store.value("ui/tts_enabled")
    store.setValue("ui/tts_enabled", False)  # no real speaker in this test
    win = MainWindow()
    yield win
    win.close()
    if before is None:
        store.remove("ui/tts_enabled")
    else:
        store.setValue("ui/tts_enabled", before)


def test_speech_timeout_scales_with_the_sentence() -> None:
    from fps_coach.ui.main_window import MainWindow

    short = MainWindow._speech_timeout("hi")
    long = MainWindow._speech_timeout("x" * 200)
    assert 10 < short < 11
    assert long == pytest.approx(40.0)


def test_a_stalled_sentence_restarts_the_engine_and_is_read_again(window, monkeypatch) -> None:
    old, new = _StubService(), _StubService()
    window._tts_service = old

    def fake_sync():
        window._tts_service = new

    monkeypatch.setattr(window, "_sync_tts", fake_sync)
    window._on_utterance_started("This is the coach voice.")
    window._speech_watch = (time.monotonic() - 999, "This is the coach voice.")
    window._check_speech_watchdog()

    assert old.abandoned is True
    assert new.texts == ["This is the coach voice."]
    assert "restarted" in window.chat_page.speech_status.text()


def test_a_sentence_that_stalls_twice_is_skipped(window, monkeypatch) -> None:
    services = [_StubService(), _StubService(), _StubService()]
    window._tts_service = services[0]

    def fake_sync():
        window._tts_service = services[1] if window._tts_service is None else services[2]

    monkeypatch.setattr(window, "_sync_tts", fake_sync)
    window._speech_watch = (time.monotonic() - 999, "same sentence.")
    window._check_speech_watchdog()
    window._speech_watch = (time.monotonic() - 999, "same sentence.")
    window._check_speech_watchdog()

    assert services[1].texts == ["same sentence."]
    assert services[2].texts == []  # not read a third time
    assert "skipped" in window.chat_page.speech_status.text()


def test_a_sentence_that_finishes_in_time_does_not_restart(window, monkeypatch) -> None:
    stub = _StubService()
    window._tts_service = stub
    restarted: list[str] = []
    monkeypatch.setattr(window, "_restart_speech", lambda text: restarted.append(text))
    window._on_utterance_started("quick reply.")
    window._on_utterance_finished()
    window._check_speech_watchdog()
    assert restarted == []
