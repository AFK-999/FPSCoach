"""AI Coach transcript as bubbles: order, plain text, theme redraw, and clearing."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def page(tmp_path, monkeypatch, qapp):
    from PySide6.QtCore import QSettings

    from fps_coach.ui import pages_coach, widgets
    from fps_coach.ui.styles import set_active_theme

    settings = QSettings(str(tmp_path / "bubbles.ini"), QSettings.Format.IniFormat)
    for module in (pages_coach, widgets):
        if hasattr(module, "_settings"):
            monkeypatch.setattr(module, "_settings", lambda: settings)
    set_active_theme("dark")
    chat = pages_coach.AIChatPage()
    yield chat
    set_active_theme("dark")


def test_messages_keep_their_order_and_plain_text(page) -> None:
    page._append("You", "Hello there.")
    page._append("Coach", "Try a multiplier of 0.9.")
    lines = [line for line in page.transcript.toPlainText().split("\n") if line.strip()]
    assert lines.index("You: Hello there.") < lines.index("Coach: Try a multiplier of 0.9.")


def test_user_bubble_uses_the_accent_colour(page) -> None:
    from fps_coach.ui.styles import PALETTES

    page._append("Coach", "two")
    assert PALETTES["dark"]["accent"].lower() not in page.transcript.toHtml().lower()
    page._append("You", "one")
    assert PALETTES["dark"]["accent"].lower() in page.transcript.toHtml().lower()


def test_theme_change_redraws_existing_bubbles(page) -> None:
    from fps_coach.ui.styles import PALETTES, set_active_theme

    page._append("You", "still here")
    set_active_theme("light")
    page._render_transcript()
    html = page.transcript.toHtml().lower()
    assert PALETTES["light"]["accent"].lower() in html
    assert "still here" in page.transcript.toPlainText()


def test_clear_history_empties_the_bubbles(page) -> None:
    page._append("You", "gone soon")
    page.clear_history()
    assert page._transcript_items == []
    assert page.transcript.toPlainText().strip() == ""
