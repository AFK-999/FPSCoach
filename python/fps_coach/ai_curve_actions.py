"""Turn the AI assistant's reply into a concrete curve adjustment.

The assistant is asked to tag any curve recommendation with a machine-readable
directive, e.g. ``[ADJUST: fast_flicks]`` or ``[ADJUST: sensitivity +10%]``.
:func:`parse_adjustment` extracts that directive (or falls back to actionable
free text) and returns a plan the app can apply automatically:

* ``{"kind": "preset", "key": ...}``        -> curve_adjust.apply_preset
* ``{"kind": "sensitivity", "factor": ...}`` -> curve_adjust.scale_sensitivity

Returns ``None`` when the reply is not actually recommending a change, so plain
explanations never move the curve by accident. Pure/standard-library only.
"""

from __future__ import annotations

import re

from fps_coach.curve_adjust import CurveProfile, apply_preset, scale_sensitivity

_DIRECTIVE = re.compile(r"\[ADJUST:\s*([^\]]+)\]", re.IGNORECASE)

# Words that signal the reply is actually recommending a change (used only for
# the free-text fallback, so explanations don't trigger an adjustment).
_ACTION_STEMS = (
    "increas", "rais", "higher", "boost", "lower", "reduc", "decreas",
    "set ", "adjust", "apply", "switch", "use ", "try ", "recommend",
    "optimi", "faster", "slower", "turn up", "turn down", "bump",
)

_PRESET_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("flick", "snap"), "fast_flicks"),
    (("precision", "micro", "fine aim", "tighten", "tighter"), "precision"),
    (("track", "tracking", "smooth"), "tracking"),
    (("reset", "revert", "original", "undo", "default"), "reset"),
]

_RAISE = ("increas", "rais", "higher", "boost", "more sens", "faster", "turn up", "bump up", "up ")
_LOWER = ("lower", "reduc", "decreas", "less sens", "slower", "turn down", "down ")


def _sensitivity_factor(scope: str) -> float | None:
    # Explicit multiplier: "x1.1" or "1.1x"
    mult = re.search(r"x\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*x", scope)
    if mult:
        value = float(mult.group(1) or mult.group(2))
        if 0.5 <= value <= 2.0:
            return value
    # Percentage: "+10%", "10%"
    pct = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", scope)
    if pct:
        magnitude = float(pct.group(1))
        if any(w in scope for w in _LOWER) and magnitude > 0:
            return 1.0 - magnitude / 100.0
        return 1.0 + magnitude / 100.0
    # Directional words only.
    if any(w in scope for w in _RAISE):
        return 1.08
    if any(w in scope for w in _LOWER):
        return 0.92
    return None


def parse_adjustment(text: str) -> dict | None:
    """Return an adjustment plan from an assistant reply, or None."""
    if not text:
        return None
    directive = _DIRECTIVE.search(text)
    scope = directive.group(1).lower() if directive else text.lower()
    actionable = directive is not None or any(stem in text.lower() for stem in _ACTION_STEMS)
    if not actionable:
        return None

    # Sensitivity change takes priority when the scope mentions it.
    if "sens" in scope or "%" in scope or re.search(r"x\s*\d|\d\s*x", scope):
        factor = _sensitivity_factor(scope)
        if factor is not None:
            return {"kind": "sensitivity", "factor": round(factor, 4)}

    for keywords, key in _PRESET_KEYWORDS:
        if any(k in scope for k in keywords):
            return {"kind": "preset", "key": key}

    return None


def apply_adjustment(base: CurveProfile, plan: dict) -> tuple[CurveProfile, str]:
    """Apply a parsed plan to the base curve -> (new_profile, note).

    Raises ValueError for an unknown plan.
    """
    kind = plan.get("kind")
    if kind == "preset":
        return apply_preset(base, plan["key"])
    if kind == "sensitivity":
        return scale_sensitivity(base, float(plan["factor"]))
    raise ValueError(f"Unknown adjustment plan: {plan!r}")
