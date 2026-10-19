"""Adjust an existing (imported) curve instead of generating a brand-new one.

Two entry points:

* :func:`adjust_from_recommendation` - take the user's imported base curve and
  nudge it using a completed calibration recommendation (scale by the suggested
  sensitivity and gently bend the shape toward the model's suggestion), keeping
  the base curve's own speed points and structure.

* :func:`apply_preset` - deterministic quick-actions ("optimise for fast flicks",
  "precision", "smooth tracking", raise/lower sensitivity, reset) used by the
  chat quick-action bubbles so the user can apply a configuration in one click.

Both return ``(new_profile, note)`` where ``note`` is a short human explanation
of what changed. Everything operates on deep copies - the base is never mutated.

:func:`normalized_overlay` returns before/after point lists on a *shared* scale
so the chart can draw the previous and current curves against each other.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from fps_coach.curve_io import CurveProfile

_MIN_GAIN = 0.05
_MAX_GAIN = 20.0


def _clamp_gain(value: float) -> float:
    return max(_MIN_GAIN, min(_MAX_GAIN, value))


def _max_speed(profile: CurveProfile) -> float:
    return max((p.speed for p in profile.horizontal), default=1.0) or 1.0


def _map_multipliers(
    profile: CurveProfile, fn: Callable[[float, float], float]
) -> CurveProfile:
    """Return a copy of ``profile`` with every multiplier replaced by
    ``fn(normalized_speed, multiplier)`` on both axes."""
    new = deepcopy(profile)
    for axis in (new.horizontal, new.vertical):
        span = max((p.speed for p in axis), default=1.0) or 1.0
        for point in axis:
            x = point.speed / span
            point.multiplier = _clamp_gain(fn(x, point.multiplier))
    return new


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation of ys over sorted xs at x (clamped to the ends)."""
    if not xs:
        return 1.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            if x1 - x0 < 1e-9:
                return y1
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return ys[-1]


def adjust_from_recommendation(
    base: CurveProfile,
    recommendation: dict,
    shape_blend: float = 0.4,
    name_suffix: str = " (calibrated)",
) -> tuple[CurveProfile, str]:
    """Adjust the imported ``base`` curve using a calibration ``recommendation``.

    The base curve's speed points are kept; multipliers are scaled by the
    recommended sensitivity and gently bent toward the recommended shape.
    """
    raw = list(recommendation.get("points") or [])
    sens = float(recommendation.get("sensitivity_multiplier", 1.0) or 1.0)

    # Build the recommended gain shape g(x) = sens * y / x over normalized x.
    gx: list[float] = []
    gy: list[float] = []
    for x, y in raw:
        x = float(x)
        y = float(y)
        if x > 1e-9:
            gx.append(x)
            gy.append(sens * y / x)
    # Reference gain near the middle of the curve, used to make the shape relative.
    mid_gain = _interp(gx, gy, 0.5) if gx else sens
    if mid_gain < 1e-9:
        mid_gain = sens or 1.0

    def fn(x: float, mult: float) -> float:
        rel = (_interp(gx, gy, x) / mid_gain) if gx else 1.0
        shape = 1.0 + shape_blend * (rel - 1.0)
        return mult * sens * shape

    new = _map_multipliers(base, fn)
    new.name = f"{base.name}{name_suffix}"
    new.description = (
        f"Adjusted from imported curve '{base.name}' using calibration "
        f"(sensitivity x{sens:.2f}, shape blended toward the measured recommendation)."
    )
    note = (
        f"Adjusted your imported curve '{base.name}' instead of replacing it: "
        f"scaled by the recommended sensitivity (x{sens:.2f}) and gently bent its "
        f"shape toward the calibration result."
    )
    return new, note


# --- Quick-action presets --------------------------------------------------

def _preset_fast_flicks(x: float, mult: float) -> float:
    # Raise high-speed response so fast flicks accelerate more.
    return mult * (1.0 + 0.45 * x) * 1.03


def _preset_precision(x: float, mult: float) -> float:
    # Soften low-speed response for finer micro-adjustments.
    return mult * (0.80 + 0.20 * x)


def _preset_raise_sens(x: float, mult: float) -> float:
    return mult * 1.08


def _preset_lower_sens(x: float, mult: float) -> float:
    return mult * 0.92


def _preset_identity(x: float, mult: float) -> float:
    return mult


def _apply_tracking(base: CurveProfile) -> CurveProfile:
    """Flatten the curve toward its average gain for smoother tracking."""
    new = deepcopy(base)
    for axis in (new.horizontal, new.vertical):
        if not axis:
            continue
        mean = sum(p.multiplier for p in axis) / len(axis)
        for point in axis:
            point.multiplier = _clamp_gain(0.5 * point.multiplier + 0.5 * mean)
    return new


# key -> (label, description, builder)
PRESETS: dict[str, dict] = {
    "fast_flicks": {
        "label": "Optimize for fast flicks",
        "note": "Raised high-speed response so quick flicks accelerate faster.",
        "fn": _preset_fast_flicks,
    },
    "precision": {
        "label": "Precision micro-aim",
        "note": "Softened low-speed response for finer, more controlled micro-aim.",
        "fn": _preset_precision,
    },
    "tracking": {
        "label": "Smooth tracking",
        "note": "Flattened the curve toward a steadier response for smoother tracking.",
        "fn": None,  # special-cased
    },
    "raise_sens": {
        "label": "Raise sensitivity",
        "note": "Raised overall sensitivity by about 8%.",
        "fn": _preset_raise_sens,
    },
    "lower_sens": {
        "label": "Lower sensitivity",
        "note": "Lowered overall sensitivity by about 8%.",
        "fn": _preset_lower_sens,
    },
    "reset": {
        "label": "Reset to imported curve",
        "note": "Reverted to your imported base curve with no adjustments.",
        "fn": _preset_identity,
    },
}


def preset_labels() -> list[tuple[str, str]]:
    """Return (key, label) pairs in display order for building quick-action UI."""
    return [(key, PRESETS[key]["label"]) for key in PRESETS]


def scale_sensitivity(
    base: CurveProfile, factor: float, min_factor: float = 0.5, max_factor: float = 2.0
) -> tuple[CurveProfile, str]:
    """Scale every multiplier on the imported curve by ``factor`` (bounded).

    Used when the AI recommends an explicit sensitivity change (e.g. '+10%').
    """
    factor = max(min_factor, min(max_factor, float(factor)))
    new = _map_multipliers(base, lambda _x, mult: mult * factor)
    new.name = f"{base.name} (sensitivity x{factor:.2f})"
    note = f"Scaled sensitivity by x{factor:.2f}."
    new.description = f"{note} (based on imported curve '{base.name}')."
    return new, note


def apply_preset(base: CurveProfile, key: str) -> tuple[CurveProfile, str]:
    """Apply a named quick-action preset to the imported ``base`` curve."""
    if key not in PRESETS:
        raise KeyError(f"Unknown preset: {key}")
    spec = PRESETS[key]
    if key == "tracking":
        new = _apply_tracking(base)
    elif key == "reset":
        new = deepcopy(base)
    else:
        new = _map_multipliers(base, spec["fn"])
    label = spec["label"]
    if key == "reset":
        new.name = base.name
    else:
        new.name = f"{base.name} ({label.lower()})"
    new.description = f"{spec['note']} (based on imported curve '{base.name}')."
    return new, spec["note"]


# --- Charting helpers ------------------------------------------------------

def normalized_overlay(
    previous: CurveProfile, current: CurveProfile
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Return (previous_points, current_points) normalized to a SHARED scale so
    the two curves can be compared directly on the chart (scaling differences
    stay visible, unlike per-curve normalization)."""
    speed = max(_max_speed(previous), _max_speed(current)) or 1.0
    gain = max(
        max((p.multiplier for p in previous.horizontal), default=1.0),
        max((p.multiplier for p in current.horizontal), default=1.0),
        1e-9,
    )
    prev_pts = [(p.speed / speed, p.multiplier / gain) for p in previous.horizontal]
    cur_pts = [(p.speed / speed, p.multiplier / gain) for p in current.horizontal]
    return prev_pts, cur_pts
