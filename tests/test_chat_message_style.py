"""Auto Coach notes are quiet, while conversation messages use rounded bubbles."""
from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    yield page
    page.close()


def test_automatic_and_live_notes_are_unboxed_and_muted(page):
    from fps_coach.ui.styles import active_palette

    for who in ("Coach (auto)", "Coach (live)"):
        page._append(who, "An automatic update.")
    for frame, label in page.transcript._entries:
        assert frame.parentWidget().property("messageKind") == "auto"
        assert "background: transparent" in frame.styleSheet()
        assert "border: none" in frame.styleSheet()
        assert active_palette()["text2"] in label.styleSheet()


def test_conversation_uses_rounded_contrasting_bubbles(page):
    page._append("You", "My question")
    page._append("Coach", "My response")
    user, coach = [frame for frame, _ in page.transcript._entries]
    assert "border-radius: 16px" in user.styleSheet()
    assert "border-radius: 16px" in coach.styleSheet()
    assert user.styleSheet() != coach.styleSheet()
    assert user.parentWidget().property("messageKind") == "user"
    assert coach.parentWidget().property("messageKind") == "coach"


def test_proactive_ai_reply_is_automatic_and_normal_reply_is_not(page):
    page._proactive_received("Automatic recommendation")
    page._on_reply("Answer to your question")
    assert [frame.parentWidget().property("messageKind") for frame, _ in page.transcript._entries] == ["auto", "coach"]


def test_theme_refresh_and_font_zoom_keep_message_roles_and_content(page):
    from fps_coach.ui.styles import PALETTES, set_active_theme

    old_size = page._settings.value("chat/font_px")
    page._append("Coach (auto)", "Automatic message")
    page._append("You", "Literal <markup> & text")
    page._append("Coach", "Reply")
    try:
        set_active_theme("light")
        page.refresh_from_settings()
        page.font_slider.setValue(22)
        assert [frame.parentWidget().property("messageKind") for frame, _ in page.transcript._entries] == ["auto", "user", "coach"]
        assert PALETTES["light"]["text2"] in page.transcript._entries[0][1].styleSheet()
        assert all("font-size: 22px" in label.styleSheet() for _, label in page.transcript._entries)
        assert "Literal <markup> & text" in page.transcript.toPlainText()
        page.clear_history()
        assert not page.transcript._entries
        assert page.transcript.toPlainText() == ""
    finally:
        set_active_theme("dark")
        if old_size is None:
            page._settings.remove("chat/font_px")
        else:
            page._settings.setValue("chat/font_px", old_size)


def test_long_messages_wrap_and_remain_visible_at_large_text_size(page, qapp):
    page.resize(1000, 700)
    page.show()
    page.font_slider.setValue(24)
    page._append("Coach", "This is a long response with lots of words. " * 30)
    for _ in range(8):
        qapp.processEvents()
    frame, label = page.transcript._entries[0]
    assert label.height() >= label.heightForWidth(label.width())
    assert frame.width() <= page.transcript.viewport().width()
    assert page.transcript.verticalScrollBar().maximum() > 0


def test_speaker_labels_are_bold_and_message_text_is_plain(page):
    from PySide6.QtGui import QFont, QTextCursor, QTextDocument

    content = 'Literal  <b>not bold</b> & "safe"\n  second line'
    page._append("You", content)
    page._append("Coach", "Response")
    page._append("Coach (auto)", "Quiet notice")
    for (_, label), who, text in zip(page.transcript._entries,
                                     ("You", "Coach", "Coach (auto)"),
                                     (content, "Response", "Quiet notice"), strict=True):
        document = QTextDocument()
        document.setHtml(label.text())
        assert document.toPlainText() == f"{who}: {text}"
        cursor = QTextCursor(document)
        cursor.setPosition(0)
        cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
        assert cursor.charFormat().fontWeight() >= QFont.Weight.Bold
        cursor.setPosition(len(who) + 2)
        cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
        assert cursor.charFormat().fontWeight() < QFont.Weight.Bold
    assert page.transcript.toPlainText() == f"You: {content}\nCoach: Response\nCoach (auto): Quiet notice"
    document = page.transcript.document()
    for role in ("You", "Coach"):
        cursor = document.find(role)
        assert cursor.charFormat().fontWeight() >= QFont.Weight.Bold


def test_user_bubble_is_slightly_darker_with_readable_text_in_both_themes(page):
    import re

    from fps_coach.ui.styles import set_active_theme
    from PySide6.QtGui import QColor

    def luminance(color):
        rgb = [color.redF(), color.greenF(), color.blueF()]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]

    try:
        for theme, old_background in (("dark", "#26352e"), ("light", "#34483b")):
            set_active_theme(theme)
            page._append("You", f"{theme} message")
            frame, label = page.transcript._entries[-1]
            background = QColor(re.search(r"background: (#[0-9a-fA-F]{6})", frame.styleSheet()).group(1))
            text = QColor(re.search(r"color: (#[0-9a-fA-F]{6})", label.styleSheet()).group(1))
            assert luminance(background) < luminance(QColor(old_background))
            assert (luminance(text) + .05) / (luminance(background) + .05) >= 4.5
    finally:
        set_active_theme("dark")
