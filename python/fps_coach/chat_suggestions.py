"""Dynamic quick-action suggestions for the AI chat.

As the user types, :func:`suggest` returns a small, topic-aligned list of
quick actions. Each suggestion is one of:

* ``kind="preset"``  - a one-click curve adjustment (key into curve_adjust.PRESETS);
  only offered when an imported base curve is available.
* ``kind="prompt"``  - a ready-made question to drop into the chat box.

Pure and dependency-light so it can be unit-tested without Qt.
"""

from __future__ import annotations

from fps_coach.curve_adjust import PRESETS

# (keywords, preset_key_or_None, (prompt_label, prompt_text))
_RULES: list[tuple[tuple[str, ...], str | None, tuple[str, str]]] = [
    (("flick", "flicks", "flicking", "snap"), "fast_flicks",
     ("Ask about flicks", "How can I improve my flick accuracy?")),
    (("track", "tracking", "follow", "strafe"), "tracking",
     ("Ask about tracking", "How can I track moving targets more smoothly?")),
    (("precision", "precise", "micro", "fine", "tight"), "precision",
     ("Ask about precision", "How do I tighten my micro-adjustments?")),
    (("overshoot", "overshooting", "past the", "too far"), "precision",
     ("Ask about overshoot", "Why do I keep overshooting targets?")),
    (("undershoot", "short", "stopping short", "reach"), "raise_sens",
     ("Ask about undershoot", "I keep stopping short of targets - what helps?")),
    (("curve", "adjust", "tune", "config", "profile"), None,
     ("Ask about my curve", "How should I adjust my current curve?")),
    (("sensitivity", "sens", "dpi"), None,
     ("Ask about sensitivity", "Is my sensitivity right for me?")),
    (("recoil", "spray", "control"), None,
     ("Ask about recoil", "How do I control my spray/recoil better?")),
    (("warm", "warmup", "routine", "practice", "drill"), None,
     ("Suggest a drill", "Suggest a 10-minute warmup drill for my aim.")),
]

# Extra sensitivity direction presets.
_RAISE = ("faster", "too slow", "more sens", "higher sens", "speed up")
_LOWER = ("slower", "too fast", "less sens", "lower sens", "slow down")


def suggest(text: str, has_base_curve: bool, limit: int = 4) -> list[dict]:
    """Return up to ``limit`` topic-aligned suggestions for the current text."""
    t = (text or "").lower()
    out: list[dict] = []

    def add_preset(key: str) -> None:
        if has_base_curve and key in PRESETS:
            out.append({"label": PRESETS[key]["label"], "kind": "preset", "payload": key})

    def add_prompt(label: str, prompt: str) -> None:
        out.append({"label": label, "kind": "prompt", "payload": prompt})

    matched = False
    for keywords, preset, (plabel, pprompt) in _RULES:
        if any(k in t for k in keywords):
            matched = True
            if preset:
                add_preset(preset)
            add_prompt(plabel, pprompt)

    if any(k in t for k in _RAISE):
        matched = True
        add_preset("raise_sens")
    if any(k in t for k in _LOWER):
        matched = True
        add_preset("lower_sens")

    if not matched:
        # Sensible defaults when the topic is unclear or the box is empty.
        if has_base_curve:
            add_preset("fast_flicks")
            add_preset("precision")
        add_prompt("Explain my results", "Explain my latest calibration results in simple terms.")
        add_prompt("Suggest a drill", "Suggest a 10-minute warmup drill for my aim.")

    # De-duplicate (keep order) and cap.
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for s in out:
        key = (s["kind"], s["payload"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique[:limit]
