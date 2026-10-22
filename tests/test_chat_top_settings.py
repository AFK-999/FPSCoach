"""AI Coach top settings: Read aloud and Replies controls sit at the top, above the transcript."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def page(qapp):
    from fps_coach.ui.pages_coach import AIChatPage
    from fps_coach.ui.widgets import _settings

    store = _settings()
    keys = ("chat/font_px", "ui/tts_enabled")
    before = {k: store.value(k) for k in keys}
    p = AIChatPage()
    p.resize(1100, 1400)
    p.show()
    qapp.processEvents()
    yield p
    p.close()
    for key, value in before.items():
        if value is None:
            store.remove(key)
        else:
            store.setValue(key, value)
    store.sync()


def _y(page, widget) -> int:
    return widget.mapTo(page, widget.rect().topLeft()).y()


def test_read_aloud_and_reply_text_size_sit_above_the_transcript(page) -> None:
    assert _y(page, page.read_aloud) < _y(page, page.transcript)
    assert _y(page, page.font_slider) < _y(page, page.transcript)


def test_replies_controls_come_before_the_voice_section(page) -> None:
    assert _y(page, page.read_aloud) < _y(page, page.font_slider) < _y(page, page.voice_settings_toggle)


def test_quick_actions_stay_below_the_transcript(page) -> None:
    buttons = [page.suggestion_bar.itemAt(i).widget() for i in range(page.suggestion_bar.count())]
    buttons = [b for b in buttons if b is not None]
    assert buttons, "expected the quick action buttons to exist"
    for button in buttons:
        assert _y(page, button) > _y(page, page.transcript)


def test_section_headings_are_present(page) -> None:
    from PySide6.QtWidgets import QLabel

    texts = [label.text() for label in page.findChildren(QLabel)]
    assert "Replies" in texts
    assert "Voice" in texts


def test_controls_keep_their_behaviour(page) -> None:
    # Moving the controls must not change what they do.
    page.font_slider.setValue(18)
    assert page.font_slider.value() == 18
    page.read_aloud.setChecked(True)
    assert page.read_aloud.isChecked()


def test_voice_settings_are_collapsed_behind_a_gear(page) -> None:
    assert page.voice_settings.isHidden()
    assert page.voice_settings_toggle.isCheckable()
    page.voice_settings_toggle.setChecked(True)
    assert not page.voice_settings.isHidden()
    page.voice_settings_toggle.setChecked(False)
    assert page.voice_settings.isHidden()


def test_speed_volume_voice_and_test_live_in_the_voice_settings(page) -> None:
    for widget in (page.speed_slider, page.volume_slider, page.voice_combo, page.test_voice_btn):
        assert page.voice_settings.isAncestorOf(widget)
    assert not page.voice_settings.isAncestorOf(page.read_aloud)
    assert not page.voice_settings.isAncestorOf(page.font_slider)


def test_pitch_note_is_inside_the_voice_settings(page) -> None:
    from PySide6.QtWidgets import QLabel

    notes = [w for w in page.findChildren(QLabel) if "Pitch is not available" in w.text()]
    assert notes, "pitch note missing"
    assert page.voice_settings.isAncestorOf(notes[0])


def test_gear_tooltip_shows_speed_and_volume(page) -> None:
    page.speed_slider.setValue(150)
    page.volume_slider.setValue(70)
    tip = page.voice_settings_toggle.toolTip()
    assert "speed 1.50x" in tip and "volume 70%" in tip
