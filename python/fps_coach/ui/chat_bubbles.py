"""Chat bubbles for the AI Coach transcript.

The user's messages sit on the right in an accent bubble. The coach's replies and the app's
notices sit on the left in a neutral bubble. Each bubble is HTML for the transcript's
QTextEdit, so it stays a single read-only text document (see AIChatPage._append).
"""

from __future__ import annotations

from html import escape

USER_LABEL = "You"

# Width kept empty on the side a bubble does not sit on, so bubbles never span the whole row.
_GUTTER = "22%"


def is_user_message(who: str) -> bool:
    """True for the messages the user typed; False for the coach, notices and errors."""
    return who == USER_LABEL


def message_html(who: str, text: str, palette: dict[str, str]) -> str:
    """One message as a bubble on its side, followed by a small gap.

    The speaker label sits inline before the text ("Coach (auto): ..."), as the transcript always
    showed it. Line breaks inside the text stay as line breaks, and no new paragraph is started,
    so the transcript's plain text keeps the label and the first line of text together.
    """
    mine = is_user_message(who)
    if mine:
        background, foreground, label = palette["accent"], palette["accent_ink"], palette["accent_ink"]
    else:
        background, foreground, label = palette["raised"], palette["text"], palette["text2"]
    body = escape(text).replace("\n", "<br>")
    bubble = (
        f'<table cellspacing="0" cellpadding="9" bgcolor="{background}"><tr><td>'
        f'<span style="color:{foreground}">'
        f'<span style="color:{label};font-weight:600">{escape(who)}:</span> '
        f"{body}</span></td></tr></table>"
    )
    if mine:
        row = f'<td width="{_GUTTER}"></td><td align="right">{bubble}</td>'
    else:
        row = f'<td align="left">{bubble}</td><td width="{_GUTTER}"></td>'
    gap = '<table cellspacing="0" cellpadding="0"><tr><td height="8"></td></tr></table>'
    return f'<table width="100%" cellspacing="0" cellpadding="0"><tr>{row}</tr></table>{gap}'
