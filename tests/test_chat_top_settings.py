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


def test_replies_controls_come_before_the_voice_controls(page) -> None:
    assert _y(page, page.read_aloud) < _y(page, page.font_slider) < _y(page, page.voice_combo)


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
