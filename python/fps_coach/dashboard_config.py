"""Configuration for the customizable Dashboard.

The dashboard shows a set of tool shortcuts the user can show/hide and reorder to
fit their workflow. This module holds the catalog of tools and pure helpers to
normalize a saved layout (order + enabled set) against the current catalog, so
adding/removing tools in future never corrupts a stored layout.

Pure/standard-library only - unit-tested without Qt.
"""

from __future__ import annotations

# key -> (label, one-line description). Order here is the default order.
TOOLS: dict[str, tuple[str, str]] = {
    "guide": ("Beginner guide", "How to measure, adjust, and verify safely."),
    "capture": ("Capture", "Low-latency screen capture and window selection."),
    "calibration": ("Calibration", "Run the 12-target movement task."),
    "training": ("Training & analytics", "Scenario drills plus aim accuracy and reaction-time trends."),
    "recommendation": ("Recommendation", "View, edit, and export your response curve."),
    "crosshair": ("Crosshair", "Design and export a custom crosshair."),
    "chat": ("AI chat", "Ask the coach and auto-apply curve tweaks."),
    "privacy": ("Privacy & limits", "What the app does - and never does."),
    "settings": ("Settings", "Appearance, theme, and performance."),
}

DEFAULT_ORDER: list[str] = list(TOOLS.keys())
# Tiles shown by default (a focused, low-clutter starting set).
DEFAULT_ENABLED: list[str] = [
    "capture", "calibration", "training", "recommendation", "crosshair", "chat",
]


def tool_label(key: str) -> str:
    return TOOLS[key][0] if key in TOOLS else key


def tool_description(key: str) -> str:
    return TOOLS[key][1] if key in TOOLS else ""


def normalize_order(saved_order: list[str] | None) -> list[str]:
    """Return a valid, complete order: keep saved known keys (in order), drop
    unknown ones, then append any catalog tools missing from the saved list."""
    saved = [k for k in (saved_order or []) if k in TOOLS]
    seen = set(saved)
    for key in DEFAULT_ORDER:
        if key not in seen:
            saved.append(key)
            seen.add(key)
    return saved


def normalize_enabled(saved_enabled: list[str] | None) -> set[str]:
    """Return the enabled set, filtered to known tools. ``None`` -> defaults."""
    if saved_enabled is None:
        return set(DEFAULT_ENABLED)
    return {k for k in saved_enabled if k in TOOLS}


def move(order: list[str], key: str, delta: int) -> list[str]:
    """Return a copy of ``order`` with ``key`` moved by ``delta`` positions."""
    if key not in order:
        return list(order)
    items = list(order)
    index = items.index(key)
    new_index = max(0, min(len(items) - 1, index + delta))
    if new_index == index:
        return items
    items.pop(index)
    items.insert(new_index, key)
    return items
