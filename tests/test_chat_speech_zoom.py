"""AI Coach: read-aloud switch and stop button in the chat, and the transcript text-size slider.

The real engine (pyttsx3 or PowerShell) cannot produce sound in CI or in this sandbox,
so the speaker thread is tested with a fake engine that records what it was asked to say.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def styled(qapp):
    """Load the real app stylesheet. It sets font-size on every widget, which is what
    overrides a plain setFont(); the text-size tests must run under it to mean anything."""
    from fps_coach.ui.styles import APP_STYLE

    qapp.setStyleSheet(APP_STYLE)
    yield qapp
    qapp.setStyleSheet("")


@pytest.fixture
def saved_settings(qapp):
    """Save and restore the two keys these tests change, so the user's values survive."""
    from fps_coach.ui.widgets import _settings

    store = _settings()
    keys = ("ui/tts_enabled", "chat/font_px")
    before = {key: store.value(key) for key in keys}
    yield store
    for key, value in before.items():
        if value is None:
            store.remove(key)
        else:
            store.setValue(key, value)
    store.sync()


class _FakeSpeaker:
    name = "fake speaker"

    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)

    def close(self) -> None:
        pass


def _use_fake_engine(monkeypatch, speaker: _FakeSpeaker) -> None:
    """Replace dependency install and engine start so no pip, audio, or pyttsx3 runs."""
    from fps_coach import tts_service

    monkeypatch.setattr(tts_service, "ensure_dependency", lambda progress=None, timeout=600.0: True)
    monkeypatch.setattr(
        tts_service,
        "init_engine",
        lambda platform=None, progress=None: tts_service.InitResult(speaker=speaker),
    )


def _wait_until(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# -- transcript text size -------------------------------------------------------

def test_text_size_defaults_and_applies_to_transcript(styled, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    saved_settings.remove("chat/font_px")
    page = AIChatPage()
    assert page.font_slider.value() == AIChatPage.TEXT_DEFAULT
    assert page.transcript.font().pixelSize() == AIChatPage.TEXT_DEFAULT
    assert page.transcript.document().defaultFont().pixelSize() == AIChatPage.TEXT_DEFAULT


def test_slider_changes_transcript_font_and_saves_it(styled, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page.font_slider.setValue(20)
    styled.processEvents()
    assert page.transcript.font().pixelSize() == 20
    assert page.transcript.document().defaultFont().pixelSize() == 20
    assert saved_settings.value("chat/font_px", type=int) == 20


def test_slider_readout_shows_exact_pixel_size(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page.font_slider.setValue(17)
    assert page.font_slider._readout.text() == "17 px"


def test_saved_text_size_is_restored_and_clamped(styled, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    saved_settings.setValue("chat/font_px", 19)
    page = AIChatPage()
    assert page.font_slider.value() == 19
    assert page.transcript.font().pixelSize() == 19

    saved_settings.setValue("chat/font_px", 999)
    page.refresh_from_settings()
    assert page.font_slider.value() == AIChatPage.TEXT_MAX
    assert page.transcript.font().pixelSize() == AIChatPage.TEXT_MAX


def test_reset_returns_text_size_to_default(styled, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page.font_slider.setValue(22)
    page.font_reset_btn.click()
    assert page.font_slider.value() == AIChatPage.TEXT_DEFAULT
    assert page.transcript.font().pixelSize() == AIChatPage.TEXT_DEFAULT


def test_text_size_applies_to_messages_already_in_transcript(styled, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page._append("Coach", "Hello there.")
    page.font_slider.setValue(AIChatPage.TEXT_MAX)
    assert "Hello there." in page.transcript.toPlainText()
    assert page.transcript.document().defaultFont().pixelSize() == AIChatPage.TEXT_MAX


# -- read aloud in the chat -----------------------------------------------------

def test_read_aloud_switch_saves_setting_and_emits(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    saved_settings.setValue("ui/tts_enabled", False)
    page = AIChatPage()
    changes: list[bool] = []
    page.read_aloud_changed.connect(changes.append)

    page.read_aloud.setChecked(True)
    assert changes == [True]
    assert saved_settings.value("ui/tts_enabled", False, type=bool) is True
    assert page.speech_status.text() == "Replies are read aloud."

    page.read_aloud.setChecked(False)
    assert changes == [True, False]
    assert saved_settings.value("ui/tts_enabled", True, type=bool) is False
    assert page.speech_status.text() == "Replies are shown as text only."


def test_refresh_from_settings_does_not_emit(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    changes: list[bool] = []
    page.read_aloud_changed.connect(changes.append)
    saved_settings.setValue("ui/tts_enabled", True)
    page.refresh_from_settings()
    assert page.read_aloud.isChecked() is True
    assert changes == []


def test_stop_speaking_button_emits_request(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    requests: list[bool] = []
    page.stop_speaking_requested.connect(lambda: requests.append(True))
    page.stop_speaking_btn.click()
    assert requests == [True]


def test_failed_speech_says_so_in_the_chat(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page.set_speech_failed()
    assert "unavailable" in page.speech_status.text()


# -- speaker thread --------------------------------------------------------------

def test_clear_drops_queued_replies_only(qapp) -> None:
    from fps_coach.ui.threads import _TtsService

    service = _TtsService()
    for text in ("first", "second", "third"):
        service.enqueue(text)
    assert service.clear() == 3
    assert service._queue.empty()
    assert service.clear() == 0


def test_queued_reply_is_spoken_by_the_engine(qapp, monkeypatch) -> None:
    from fps_coach.ui.threads import _TtsService

    speaker = _FakeSpeaker()
    _use_fake_engine(monkeypatch, speaker)
    service = _TtsService()
    service.start()
    service.enqueue("Aim a little higher on the second target.")
    assert _wait_until(lambda: speaker.said == ["Aim a little higher on the second target."])
    service.stop()
    assert service.wait(5000)


def test_engine_failure_is_reported_not_raised(qapp, monkeypatch) -> None:
    from fps_coach import tts_service
    from fps_coach.ui.threads import _TtsService

    monkeypatch.setattr(tts_service, "ensure_dependency", lambda progress=None, timeout=600.0: False)
    monkeypatch.setattr(
        tts_service,
        "init_engine",
        lambda platform=None, progress=None: tts_service.InitResult(
            speaker=None, attempts=["pyttsx3 default driver: no voices"]
        ),
    )
    failures: list[str] = []
    service = _TtsService()
    service.failed.connect(failures.append)
    service.start()
    assert service.wait(5000)
    qapp.processEvents()
    assert failures and "Text-to-speech is unavailable" in failures[0]


# -- main window: the chat switch drives the speaker ------------------------------

def test_chat_switch_turns_speech_on_and_off_across_the_app(qapp, monkeypatch, saved_settings) -> None:
    from fps_coach.ui.main_window import MainWindow

    saved_settings.setValue("ui/tts_enabled", False)
    speaker = _FakeSpeaker()
    _use_fake_engine(monkeypatch, speaker)

    window = MainWindow()
    try:
        assert window._tts_service is None

        window.chat_page.read_aloud.setChecked(True)
        assert window._tts_service is not None
        assert window.settings_page.tts_enabled.isChecked() is True

        window._speak_response("Your flicks are consistent.")
        assert _wait_until(lambda: "Your flicks are consistent." in speaker.said)

        window.chat_page.read_aloud.setChecked(False)
        assert window._tts_service is None
        assert window.settings_page.tts_enabled.isChecked() is False
    finally:
        window.close()


def test_settings_checkbox_is_reflected_in_chat_switch(qapp, monkeypatch, saved_settings) -> None:
    from fps_coach.ui.main_window import MainWindow

    saved_settings.setValue("ui/tts_enabled", False)
    _use_fake_engine(monkeypatch, _FakeSpeaker())
    window = MainWindow()
    try:
        saved_settings.setValue("ui/tts_enabled", True)
        window._on_settings_changed()
        assert window.chat_page.read_aloud.isChecked() is True
        assert window.chat_page.speech_status.text() == "Replies are read aloud."
    finally:
        window._on_read_aloud_changed(False)
        window.close()


def test_stop_speaking_with_speech_off_says_nothing_is_queued(qapp, monkeypatch, saved_settings) -> None:
    from fps_coach.ui.main_window import MainWindow

    saved_settings.setValue("ui/tts_enabled", False)
    _use_fake_engine(monkeypatch, _FakeSpeaker())
    window = MainWindow()
    try:
        window.chat_page.stop_speaking_btn.click()
        assert "nothing is queued" in window.chat_page.speech_status.text()
    finally:
        window.close()
