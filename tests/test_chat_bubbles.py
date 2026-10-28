"""AI Coach transcript bubbles: the user's messages on the right, the coach's and notices on the left."""

from __future__ import annotations

import re

from fps_coach.ui.chat_bubbles import is_user_message, message_html
from fps_coach.ui.styles import PALETTES


def _palette():
    return PALETTES["dark"]


def test_user_message_is_right_aligned_in_the_accent_colour() -> None:
    html = message_html("You", "Hello there.", _palette())
    assert 'align="right"' in html
    assert 'align="left"' not in html
    assert f'bgcolor="{_palette()["accent"]}"' in html


def test_coach_reply_is_left_aligned_in_the_neutral_colour() -> None:
    html = message_html("Coach", "Try 0.9.", _palette())
    assert 'align="left"' in html
    assert 'align="right"' not in html
    assert f'bgcolor="{_palette()["raised"]}"' in html


def test_notices_and_errors_are_on_the_coach_side() -> None:
    for who in ("Coach (auto)", "Applied", "Quick action", "Voice", "Coach"):
        assert not is_user_message(who)
        assert 'align="left"' in message_html(who, "x", _palette())


def test_only_the_user_label_counts_as_the_user() -> None:
    assert is_user_message("You")
    assert not is_user_message("you")
    assert not is_user_message("Your note")


def test_text_is_escaped_so_markup_is_shown_as_text() -> None:
    html = message_html("You", "<b>bold</b> & 'x'", _palette())
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; " in html


def test_newlines_become_line_breaks_inside_the_bubble() -> None:
    html = message_html("Coach", "first\nsecond", _palette())
    assert "first<br>second" in html


def test_label_is_inline_with_the_text_as_before() -> None:
    # Same line as the old "<b>who:</b> text" form, so the transcript's plain text is unchanged.
    html = message_html("Coach (auto)", "Try 0.9.", _palette())
    assert "<p" not in html
    assert re.search(r"Coach \(auto\):</span> Try 0\.9\.", html)


def test_both_palettes_render() -> None:
    for name in ("dark", "light"):
        html = message_html("You", "hi", PALETTES[name])
        assert f'bgcolor="{PALETTES[name]["accent"]}"' in html
