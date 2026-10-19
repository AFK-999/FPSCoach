"""Map recognized speech to the application's command structure.

:func:`parse` turns a recognized phrase into a :class:`Command` the app can
execute (navigate a page, run calibration, apply a curve preset, toggle the coach
panel, or dictate a message to the chat). Returns ``None`` when nothing matches.

Pure/standard-library only - unit-tested without Qt or any audio stack.
"""

from __future__ import annotations

from dataclasses import dataclass

# Navigation targets must match the page keys used in MainWindow.
_NAV_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("dashboard", "home"), "dashboard"),
    (("beginner", "guide"), "guide"),
    (("capture", "screen"), "capture"),
    (("training", "drill", "drills", "practice", "analytics", "stats"), "training"),
    (("crosshair", "reticle"), "crosshair"),
    (("recommendation", "curve", "result"), "recommendation"),
    (("chat", "assistant", "coach"), "chat"),
    (("settings", "preferences", "options"), "settings"),
    (("privacy", "limits"), "privacy"),
    (("calibration", "calibrate"), "calibration"),  # last: actions checked first
]

_PRESET_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("fast flick", "flick", "flicks"), "fast_flicks"),
    (("precision", "micro"), "precision"),
    (("tracking", "smooth"), "tracking"),
]


@dataclass(slots=True)
class Command:
    kind: str   # "navigate" | "calibrate" | "preset" | "panel" | "dictate"
    value: str


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def parse(text: str, wake_word: str = "coach") -> Command | None:
    t = (text or "").strip().lower()
    if not t:
        return None

    # Dictation: "<wake> ...", "hey <wake> ...", "ask ..." -> send the remainder.
    wake = (wake_word or "coach").strip().lower() or "coach"
    prefixes = [f"hey {wake}", f"ok {wake}", f"ask {wake}", wake, "ask"]
    seen: set[str] = set()
    for prefix in prefixes:
        if prefix in seen:
            continue
        seen.add(prefix)
        if t.startswith(prefix):
            remainder = t[len(prefix):].strip(" ,:")
            if remainder:
                return Command("dictate", remainder)

    # Calibration actions (checked before plain navigation).
    if _contains(t, ("start calibration", "begin calibration", "start calibrating", "run calibration")):
        return Command("calibrate", "start")
    if _contains(t, ("reset calibration", "cancel calibration", "stop calibration", "restart calibration")):
        return Command("calibrate", "reset")

    # Coach panel.
    if _contains(t, ("show coach panel", "show panel", "show overlay", "show coach")):
        return Command("panel", "show")
    if _contains(t, ("hide coach panel", "hide panel", "hide overlay", "hide coach")):
        return Command("panel", "hide")

    # Curve presets / sensitivity.
    if _contains(t, ("reset curve", "revert curve", "undo curve")):
        return Command("preset", "reset")
    if _contains(t, ("raise sensitivity", "increase sensitivity", "higher sensitivity", "more sensitivity")):
        return Command("preset", "raise_sens")
    if _contains(t, ("lower sensitivity", "reduce sensitivity", "decrease sensitivity", "less sensitivity")):
        return Command("preset", "lower_sens")
    for keywords, key in _PRESET_KEYWORDS:
        if _contains(t, keywords):
            return Command("preset", key)

    # Navigation.
    for keywords, key in _NAV_KEYWORDS:
        if _contains(t, keywords):
            return Command("navigate", key)

    return None
