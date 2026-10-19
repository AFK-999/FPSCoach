"""speakable_text: AI replies are cleaned before the speech engine reads them.

The transcript keeps the original text. Only the spoken copy is cleaned.
"""

from __future__ import annotations

import pytest

from fps_coach.tts_service import speakable_text


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("**Aim** higher.", "Aim higher."),
        ("Lower it by 5%.", "Lower it by 5 percent."),
        ("a+b & c = d", "a plus b and c equals d."),
        ("See [the guide](https://example.com/x) now.", "See the guide now."),
        ("Use `dpi` 800.", "Use dpi 800."),
        ("Keep it up! \u2705", "Keep it up!"),
        ("Hit it \U0001F3AF", "Hit it."),
        ("Go \u2192 next", "Go next."),
        ("Sens - 0.35", "Sens, 0.35."),
        ("and/or", "and or."),
        ("snake_case", "snake case."),
        ("Table | a | b", "Table, a, b."),
        ("~~old~~ new", "old new."),
    ],
)
def test_common_markup_and_symbols(raw: str, spoken: str) -> None:
    assert speakable_text(raw) == spoken


def test_headings_and_bullets_become_sentences() -> None:
    raw = "## Plan\n- Lower sensitivity\n- Check accuracy"
    assert speakable_text(raw) == "Plan. Lower sensitivity. Check accuracy."


def test_existing_sentence_punctuation_is_kept() -> None:
    assert speakable_text("Why? Because it works; try it: now!") == "Why? Because it works; try it: now!"


def test_fenced_code_is_replaced_with_a_note() -> None:
    raw = "Code:\n```python\nprint(1)\n```\nThen stop."
    assert speakable_text(raw) == "Code: (code example omitted). Then stop."


def test_bare_url_becomes_link() -> None:
    assert speakable_text("Read https://example.com/a?b=1 first.") == "Read link first."


def test_symbol_only_text_is_empty() -> None:
    assert speakable_text("  \U0001F3AF \u2192 **  ") == ""
    assert speakable_text("") == ""
    assert speakable_text(None) == ""


def test_plain_reply_is_unchanged() -> None:
    reply = "Aim a little higher on the second target."
    assert speakable_text(reply) == reply


def test_no_markup_characters_reach_the_engine() -> None:
    raw = "## *Head* `x` [a](b) <tag> {brace} [bracket] ^caret \\ back ~tilde~ # hash $5 \u00a9 \u2122"
    spoken = speakable_text(raw)
    for bad in "*#`[]{}<>^\\~$|_\u00a9\u2122":
        assert bad not in spoken, bad
    assert "5" in spoken


def test_speech_queue_receives_cleaned_text(monkeypatch) -> None:
    """The queue is where replies enter the speaker, so cleaning must happen there."""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from fps_coach.ui.threads import _TtsService

    service = _TtsService()
    service.enqueue("**Lower** sensitivity by 5%.")
    service.enqueue("   ")  # nothing to say: not queued
    service.enqueue("\U0001F3AF")  # symbol only: not queued
    assert service._queue.get_nowait() == "Lower sensitivity by 5 percent."
    assert service._queue.empty()
