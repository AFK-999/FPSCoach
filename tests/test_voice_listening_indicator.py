"""One-shot voice input communicates preparing, listening and processing states."""
from __future__ import annotations

import sys
import types

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


class FakeVoiceThread(QObject):
    recognized = Signal(str)
    failed = Signal(str)
    status_changed = Signal(str)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):  # noqa: N802 - Qt compatibility
        return self.running

    def finish(self):
        self.running = False
        self.finished.emit()


@pytest.fixture
def page(qapp, monkeypatch):
    from fps_coach.ui import pages_coach

    monkeypatch.setattr(pages_coach, "_VoiceListenThread", FakeVoiceThread)
    page = pages_coach.AIChatPage()
    yield page
    if page._voice_thread:
        page._voice_thread.finish()
    page.close()


def test_click_shows_preparation_and_listening_and_finishes(page):
    assert page.voice_input_status.isHidden()
    assert not page.mic_btn.isChecked()
    page.mic_btn.click()
    thread = page._voice_thread
    assert thread is not None
    assert not page.voice_input_status.isHidden()
    assert "Preparing" in page.voice_input_status.text()
    assert page.mic_btn.isChecked()
    assert not page.mic_btn.isEnabled()  # one-shot capture cannot be cancelled by clicking
    thread.status_changed.emit("listening")
    assert "Listening" in page.voice_input_status.text()
    thread.status_changed.emit("processing")
    assert "Processing" in page.voice_input_status.text()
    thread.finish()
    assert page.voice_input_status.isHidden()
    assert page.mic_btn.isEnabled() and not page.mic_btn.isChecked()
    assert page._voice_thread is None
    page.mic_btn.click()
    assert page._voice_thread is not None  # another question can be recorded


def test_failure_clears_indicator_and_keeps_explanation(page):
    page.mic_btn.click()
    thread = page._voice_thread
    thread.status_changed.emit("listening")
    thread.failed.emit("No microphone was available or nothing was heard.")
    thread.finish()
    assert page.voice_input_status.isHidden()
    assert "No microphone" in page.transcript.toPlainText()
    assert page.mic_btn.isEnabled() and not page.mic_btn.isChecked()


def test_recognition_sends_question_and_clears_indicator(page, monkeypatch):
    sent = []
    monkeypatch.setattr(page, "_send", lambda: sent.append(page.input.text()))
    page.mic_btn.click()
    thread = page._voice_thread
    thread.status_changed.emit("listening")
    thread.recognized.emit("How is my aim?")
    thread.finish()
    assert sent == ["How is my aim?"]
    assert page.voice_input_status.isHidden()
    assert page.mic_btn.isEnabled()


def test_missing_voice_backend_reports_failure_and_can_finish(page, monkeypatch):
    from fps_coach import voice_control
    from fps_coach.ui.threads import _VoiceListenThread

    monkeypatch.setattr(voice_control, "available_backend", lambda: None)
    worker = _VoiceListenThread(page)
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "install" in failures[0].lower()


def test_speech_backend_emits_real_capture_and_processing_states(monkeypatch):
    from fps_coach import voice_control

    events = []

    class Microphone:
        def __enter__(self):
            events.append("mic-open")
            return self

        def __exit__(self, *_args):
            events.append("mic-closed")

    class Recognizer:
        def adjust_for_ambient_noise(self, _source, duration):
            assert duration == 0.3

        def listen(self, _source, timeout, phrase_time_limit):
            events.append("capture")
            return object()

        def recognize_google(self, _audio):
            events.append("recognize")
            return "Hello coach"

    fake_sr = types.SimpleNamespace(Recognizer=Recognizer, Microphone=Microphone)
    monkeypatch.setitem(sys.modules, "speech_recognition", fake_sr)
    monkeypatch.setattr(voice_control, "available_backend", lambda: "speech_recognition")
    result = voice_control.recognize_once(on_state=events.append)
    assert result.ok and result.text == "Hello coach"
    assert events == ["mic-open", "listening", "capture", "mic-closed", "processing", "recognize"]


def test_worker_reports_unexpected_exception(qapp, monkeypatch):
    from fps_coach import voice_control
    from fps_coach.ui.threads import _VoiceListenThread

    def fail(**_kwargs):
        raise RuntimeError("simulated audio error")

    monkeypatch.setattr(voice_control, "recognize_once", fail)
    worker = _VoiceListenThread()
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors == ["Voice input failed: simulated audio error"]
