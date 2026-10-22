"""Settings > Replies: read aloud, stop speaking and reply text size, kept in step with the AI Coach tab.

Both places read and write the same saved values (ui/tts_enabled, chat/font_px), so these tests
check the controls in each place and the sync between them. The speech engine is not started:
read-aloud is left off in the main-window tests.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def saved_settings(qapp):
    """Snapshot and restore the saved keys, so the user's values survive these tests."""
    from fps_coach.ui.widgets import _settings

    store = _settings()
    keys = ("ui/tts_enabled", "chat/font_px")
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
    chat.replies_changed.connect(settings.refresh_replies)
    yield chat, settings
    chat.close()
    settings.close()


# -- the Settings card -----------------------------------------------------------

def test_settings_has_a_replies_card_with_all_three_controls(pages) -> None:
    _chat, settings = pages
    assert settings.replies_read_aloud is not None
    assert settings.replies_stop_btn is not None
    assert settings.replies_font_slider is not None
    assert settings.replies_font_reset is not None


def test_replies_card_starts_from_the_saved_values(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import SettingsPage

    saved_settings.setValue("ui/tts_enabled", True)
    saved_settings.setValue("chat/font_px", 19)
    settings = SettingsPage()
    try:
        assert settings.replies_read_aloud.isChecked() is True
        assert settings.replies_font_slider.value() == 19
    finally:
        settings.close()


def test_settings_text_size_saves_and_announces_while_dragging(pages, saved_settings) -> None:
    _chat, settings = pages
    seen: list[int] = []
    settings.text_size_changed.connect(seen.append)

    settings.replies_font_slider.setValue(21)
    settings.replies_font_slider.setValue(22)

    assert seen == [21, 22]
    assert saved_settings.value("chat/font_px", type=int) == 22


def test_settings_reset_goes_back_to_the_default_size(pages, saved_settings) -> None:
    chat, settings = pages
    settings.replies_font_slider.setValue(24)
    settings.replies_font_reset.click()

    assert settings.replies_font_slider.value() == chat.TEXT_DEFAULT
    assert chat.font_slider.value() == chat.TEXT_DEFAULT
    assert saved_settings.value("chat/font_px", type=int) == chat.TEXT_DEFAULT


def test_settings_read_aloud_writes_the_shared_key_and_updates_voice_card(pages, saved_settings) -> None:
    _chat, settings = pages
    settings.replies_read_aloud.setChecked(True)

    assert saved_settings.value("ui/tts_enabled", type=bool) is True
    assert settings.tts_enabled.isChecked() is True

    settings.tts_enabled.setChecked(False)
    assert settings.replies_read_aloud.isChecked() is False
    assert saved_settings.value("ui/tts_enabled", type=bool) is False


def test_stop_button_emits_the_stop_signal(pages) -> None:
    _chat, settings = pages
    fired: list[bool] = []
    settings.stop_speaking_requested.connect(lambda: fired.append(True))

    settings.replies_stop_btn.click()

    assert fired == [True]


# -- the AI Coach tab drives the same values -------------------------------------

def test_chat_text_size_updates_the_settings_slider(pages, saved_settings) -> None:
    chat, settings = pages
    chat.font_slider.setValue(17)

    assert settings.replies_font_slider.value() == 17
    assert saved_settings.value("chat/font_px", type=int) == 17


def test_chat_read_aloud_updates_the_settings_checkbox(pages, saved_settings) -> None:
    _chat, settings = pages
    # Setting the chat checkbox would start the speaker in the main window, so only
    # the saved value and the refresh are checked here.
    saved_settings.setValue("ui/tts_enabled", True)
    settings.refresh_replies()

    assert settings.replies_read_aloud.isChecked() is True
    assert settings.tts_enabled.isChecked() is True


def test_set_text_size_from_settings_does_not_echo_back(pages, saved_settings) -> None:
    chat, settings = pages
    echoes: list[bool] = []
    chat.replies_changed.connect(lambda: echoes.append(True))

    chat.set_text_size(16)

    assert echoes == []
    assert chat.font_slider.value() == 16
    assert saved_settings.value("chat/font_px", type=int) == 16


def test_set_text_size_clamps_to_the_allowed_range(pages, saved_settings) -> None:
    chat, _settings = pages
    chat.set_text_size(99)
    assert chat.font_slider.value() == chat.TEXT_MAX
    chat.set_text_size(1)
    assert chat.font_slider.value() == chat.TEXT_MIN


# -- main window: the two places stay in step ------------------------------------

def test_main_window_moves_the_transcript_from_settings(qapp, saved_settings) -> None:
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.settings_page.replies_font_slider.setValue(20)
        assert window.chat_page.font_slider.value() == 20
        assert window.chat_page.transcript.styleSheet() == "font-size: 20px;"
        assert saved_settings.value("chat/font_px", type=int) == 20
    finally:
        window.close()


def test_main_window_keeps_settings_in_step_with_the_chat(qapp, saved_settings) -> None:
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.chat_page.font_slider.setValue(15)
        assert window.settings_page.replies_font_slider.value() == 15
    finally:
        window.close()


def test_stop_in_settings_runs_the_same_stop_as_the_chat(qapp, saved_settings) -> None:
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.settings_page.replies_stop_btn.click()
        assert "nothing is queued" in window.chat_page.speech_status.text()
    finally:
        window.close()
