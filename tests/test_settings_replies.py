"""Settings has no Replies card: read aloud, stop speaking and text size live in the AI Coach tab.

There used to be a second copy of those three controls on the Settings page, backed by the same
saved values. It was removed because the AI Coach tab's own Settings block already owns them.

These tests pin both halves of that decision: the duplicates are gone from Settings (including the
signals that only existed to serve them), and the single remaining copy in the AI Coach tab still
works and still agrees with the one switch Settings keeps under Voice.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def saved_settings(qapp):
    """Snapshot and restore the saved keys, so the user's values survive these tests."""
    from fps_coach.ui.widgets import _settings

    store = _settings()
    keys = ("ui/tts_enabled", "chat/font_px", "tts/volume")
    before = {key: store.value(key) for key in keys}
    store.setValue("ui/tts_enabled", False)
    store.setValue("chat/font_px", 14)
    yield store
    for key, value in before.items():
        if value is None:
            store.remove(key)
        else:
            store.setValue(key, value)
    store.sync()


@pytest.fixture()
def pages(qapp, saved_settings):
    from fps_coach.ui.pages_coach import AIChatPage, SettingsPage

    chat = AIChatPage()
    settings = SettingsPage()
    yield chat, settings
    chat.close()
    settings.close()
    # Deterministic disposal: widgets that outlive QApplication crash at interpreter exit.
    chat.deleteLater()
    settings.deleteLater()
    qapp.processEvents()


@pytest.fixture()
def windows(qapp, saved_settings):
    """Build MainWindows and dispose of them the same way, so nothing outlives Qt."""
    from fps_coach.ui.main_window import MainWindow

    made = []

    def _make():
        window = MainWindow()
        made.append(window)
        return window

    yield _make
    for window in made:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def _texts(widget) -> list[str]:
    """Every piece of text inside a page, so a leftover label cannot hide."""
    found = [label.text() for label in widget.findChildren(QLabel)]
    found += [box.text() for box in widget.findChildren(QCheckBox)]
    found += [button.text() for button in widget.findChildren(QPushButton)]
    return [text.strip() for text in found if text and text.strip()]


# -- the duplicates are gone -----------------------------------------------------

@pytest.mark.parametrize(
    "attribute",
    ["replies_read_aloud", "replies_stop_btn", "replies_font_slider", "replies_font_reset"],
)
def test_settings_no_longer_has_the_replies_controls(pages, attribute) -> None:
    _chat, settings = pages
    assert not hasattr(settings, attribute)


def test_settings_has_no_replies_card_or_heading(pages) -> None:
    _chat, settings = pages
    texts = _texts(settings)
    assert "Replies" not in texts
    assert "Read replies aloud" not in texts
    # "Reply text size" was the slider label inside the removed card.
    assert "Reply text size" not in texts


def test_settings_no_longer_carries_the_signals_that_served_the_card(pages) -> None:
    _chat, settings = pages
    for signal in ("stop_speaking_requested", "text_size_changed"):
        assert not hasattr(settings, signal)


def test_the_ai_coach_page_no_longer_advertises_a_change_to_settings(pages) -> None:
    """replies_changed existed only so the Settings copy could follow the chat."""
    chat, _settings = pages
    assert not hasattr(chat, "replies_changed")
    assert not hasattr(chat, "set_text_size")


def test_the_voice_card_keeps_its_own_speak_responses_switch(pages) -> None:
    """One switch stays in Settings, under Voice, so the feature is findable there too."""
    _chat, settings = pages
    assert settings.tts_enabled.text() == "Speak responses aloud"


# -- the single remaining copy still works ---------------------------------------

def test_the_chat_tab_has_read_aloud_stop_and_text_size(pages) -> None:
    chat, _settings = pages
    assert chat.read_aloud.text() == "Read replies aloud"
    assert chat.stop_speaking_btn.text().strip() == "Stop speaking"
    assert chat.font_slider.value() == 14
    assert chat.font_reset_btn.text() == "Reset"


def test_the_chat_text_size_saves_and_moves_the_transcript(pages, saved_settings) -> None:
    chat, _settings = pages
    chat.font_slider.setValue(21)

    assert saved_settings.value("chat/font_px", type=int) == 21
    assert chat.transcript.styleSheet() == "font-size: 21px;"


def test_the_chat_reset_returns_to_the_default_size(pages, saved_settings) -> None:
    chat, _settings = pages
    chat.font_slider.setValue(24)
    chat.font_reset_btn.click()

    assert chat.font_slider.value() == chat.TEXT_DEFAULT
    assert saved_settings.value("chat/font_px", type=int) == chat.TEXT_DEFAULT


def test_the_chat_stop_button_emits_the_stop_signal(pages) -> None:
    chat, _settings = pages
    fired: list[bool] = []
    chat.stop_speaking_requested.connect(lambda: fired.append(True))

    chat.stop_speaking_btn.click()

    assert fired == [True]


def test_the_saved_text_size_is_restored_on_start_up(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    saved_settings.setValue("chat/font_px", 18)
    chat = AIChatPage()
    try:
        assert chat.font_slider.value() == 18
        assert chat.transcript.styleSheet() == "font-size: 18px;"
    finally:
        chat.close()


# -- the one shared value still stays in step ------------------------------------

def test_the_voice_switch_and_the_chat_switch_share_one_saved_value(pages, saved_settings) -> None:
    chat, settings = pages

    chat.read_aloud.setChecked(True)
    assert saved_settings.value("ui/tts_enabled", type=bool) is True
    settings.refresh_voice_state()
    assert settings.tts_enabled.isChecked() is True

    settings.tts_enabled.setChecked(False)
    assert saved_settings.value("ui/tts_enabled", type=bool) is False


def test_the_voice_switch_is_refreshed_without_echoing(pages, saved_settings) -> None:
    _chat, settings = pages
    echoes: list[bool] = []
    settings.tts_enabled.toggled.connect(echoes.append)

    saved_settings.setValue("ui/tts_enabled", True)
    settings.refresh_voice_state()

    assert settings.tts_enabled.isChecked() is True
    assert echoes == []  # a refresh is not a user action


# -- main window wiring ----------------------------------------------------------

def test_the_main_window_still_stops_speech_from_the_chat_tab(windows) -> None:
    window = windows()
    window.chat_page.stop_speaking_btn.click()
    assert "nothing is queued" in window.chat_page.speech_status.text()


def test_the_main_window_keeps_the_voice_switch_in_step(windows, monkeypatch) -> None:
    from fps_coach.ui.main_window import MainWindow

    # The reply -> speak path is not what this test is about, and starting a real speech
    # engine would block on a machine with no audio device.
    monkeypatch.setattr(MainWindow, "_sync_tts", lambda self: None)
    window = windows()
    window.chat_page.read_aloud.setChecked(True)
    assert window.settings_page.tts_enabled.isChecked() is True
    window.chat_page.read_aloud.setChecked(False)
    assert window.settings_page.tts_enabled.isChecked() is False


def test_the_settings_page_still_exists_beside_the_chat_tab(windows) -> None:
    """Removal is only the Replies card: the rest of the Settings page is untouched."""
    window = windows()
    assert window.settings_page is not None
    texts = _texts(window.settings_page)
    for heading in ("AI coach", "Voice", "Interface", "Advanced"):
        assert heading in texts
