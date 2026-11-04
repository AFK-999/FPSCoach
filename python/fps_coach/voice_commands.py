"""Parse finalized voice phrases into explicit, bounded in-app commands.

The listener is optional. Recognized navigation/actions are matched against whole
phrases, not substrings in a longer conversation. Explicit "ask ..." is always a
chat request. A wake-word phrase that isn't a command remains a chat request.
This module uses only the standard library; it neither records nor sends audio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class Command:
    kind: str
    value: str


def _normalize(text: str) -> str:
    # Vosk often omits punctuation; matching should not depend on it.
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _action(text: str) -> Command | None:
    text = _normalize(text)
    text = re.sub(r"^(?:(?:can|could|would) you\s+|please\s+)+", "", text)
    text = re.sub(r"\s+please$", "", text)
    verb = r"(?:open|show|go to|navigate to|take me to|view)"
    close = r"(?:close|hide|collapse)"
    open_panel = r"(?:open|show|expand)"

    # More-specific destinations must precede the general navigation entries.
    for label, target in ((r"(?:calibration|calibration input|input path|input)", "calibration"),
                          (r"(?:training|target)", "training"),
                          (r"(?:ai coach|coach|chat|reply|replies)", "coach")):
        for action, pattern in (("open", open_panel), ("close", close)):
            if (re.fullmatch(rf"{pattern} (?:the )?{label} settings(?: panel| menu)?", text)
                    or re.fullmatch(rf"{pattern} (?:the )?settings (?:for|in) (?:the )?{label}", text)
                    or (target == "calibration" and re.fullmatch(
                        rf"(?:go to|open|navigate to) calibration and {pattern} (?:the )?(?:input )?settings", text))):
                return Command("section", f"{target}:{action}")

    if re.fullmatch(rf"{open_panel} (?:the )?(?:calibration )?input path", text):
        return Command("section", "calibration:open")

    for label, target in ((r"(?:voice|speech)", "voice"), (r"(?:ai|model|provider)", "ai"),
                          (r"(?:interface|appearance)", "interface"), (r"advanced", "advanced")):
        if re.fullmatch(rf"{verb} (?:the )?{label} settings", text):
            return Command("settings", target)

    if re.fullmatch(rf"{verb} (?:the )?(?:optional )?calibration (?:guide|wizard)", text) or text in (
        "start calibration guide", "start the calibration guide", "open setup guide"
    ):
        return Command("guide", "open")
    for pattern, action in (
        (r"(?:next|continue)(?: guide| calibration guide)?(?: question| step)?", "next"),
        (r"(?:previous|back)(?: guide| calibration guide)?(?: question| step)?", "back"),
        (r"(?:skip)(?: (?:the )?(?:guide|calibration guide))? (?:question|step)", "skip"),
    ):
        if re.fullmatch(pattern, text):
            return Command("guide", action)

    if re.fullmatch(rf"{verb} (?:the )?(?:sensitivity|calibration) (?:curve|graph)", text):
        return Command("curve_view", "sensitivity")
    if re.fullmatch(rf"{verb} (?:the )?(?:coach )?curve (?:tuning|preview)", text):
        return Command("coach", "curve_tuning")
    if text in ("focus coach", "focus coach input", "focus chat", "write to coach", "ask a question"):
        return Command("coach", "focus")
    if text in ("explain my calibration", "analyze my calibration", "analyse my calibration",
                "coach explain my calibration"):
        return Command("coach", "explain_calibration")
    if text in ("stop speaking", "stop talking", "stop reading", "stop reading aloud"):
        return Command("coach", "stop_speaking")

    if text in ("start calibration", "begin calibration", "start calibrating", "run calibration"):
        return Command("calibrate", "start")
    if text in ("reset calibration", "cancel calibration", "stop calibration", "restart calibration"):
        return Command("calibrate", "reset")
    if text in ("resume raw input", "resume raw calibration"):
        return Command("calibrate", "resume")
    if text in ("show coach panel", "show panel", "show overlay", "show coach"):
        return Command("panel", "show")
    if text in ("hide coach panel", "hide panel", "hide overlay", "hide coach"):
        return Command("panel", "hide")

    preset_phrases = {
        "reset": ("reset curve", "revert curve", "undo curve"),
        "raise_sens": ("raise sensitivity", "increase sensitivity", "higher sensitivity", "more sensitivity"),
        "lower_sens": ("lower sensitivity", "reduce sensitivity", "decrease sensitivity", "less sensitivity"),
        "fast_flicks": ("optimize for fast flicks", "switch to fast flicks", "fast flicks"),
        "precision": ("switch to precision", "optimize for precision", "precision"),
        "tracking": ("switch to tracking", "optimize for tracking", "tracking"),
    }
    for key, phrases in preset_phrases.items():
        if text in phrases:
            return Command("preset", key)

    targets = (
        (r"(?:dashboard|home)", "dashboard"),
        (r"(?:beginner guide|guide)", "guide"),
        (r"(?:capture|screen capture|screen)", "capture"),
        (r"(?:training|drill|drills|practice|analytics|stats)", "training"),
        (r"(?:crosshair|reticle)", "crosshair"),
        (r"(?:recommendation|recommendations|result|results|curve)", "recommendation"),
        (r"(?:ai coach|chat|assistant|coach)", "chat"),
        (r"(?:settings|preferences|options)", "settings"),
        (r"(?:privacy|limits)", "privacy"),
        (r"(?:calibration|calibrate)", "calibration"),
    )
    for pattern, target in targets:
        if re.fullmatch(rf"(?:(?:{verb}) (?:the )?)?{pattern}(?: (?:tab|page|screen))?", text):
            return Command("navigate", target)
    return None


def parse(text: str, wake_word: str = "coach") -> Command | None:
    phrase = (text or "").strip()
    if not phrase:
        return None
    wake = (wake_word or "coach").strip() or "coach"
    if _normalize(phrase) == "ask a question":
        return Command("coach", "focus")

    # "Ask" explicitly means a provider-bound chat request, never a UI command.
    ask = re.match(r"^ask\b[\s,:-]*(.*)$", phrase, re.IGNORECASE)
    if ask:
        remainder = ask.group(1).strip()
        wake_prefix = re.match(rf"^{re.escape(wake)}\b[\s,:-]*(.*)$", remainder, re.IGNORECASE)
        if wake_prefix:
            remainder = wake_prefix.group(1).strip()
        elif wake.lower() != "coach":
            coach_prefix = re.match(r"^coach\b[\s,:-]*(.*)$", remainder, re.IGNORECASE)
            if coach_prefix:
                remainder = coach_prefix.group(1).strip()
        return Command("dictate", remainder) if remainder else None

    # With or without a wake word, accept only complete recognized actions.
    prefix = re.match(rf"^(?:(?:hey|ok|okay)\s+)?{re.escape(wake)}\b[\s,:-]*(.*)$",
                      phrase, re.IGNORECASE)
    remainder = prefix.group(1).strip() if prefix else phrase
    if not remainder:
        return None
    action = _action(remainder)
    if action is not None:
        return action
    return Command("dictate", remainder) if prefix else None
