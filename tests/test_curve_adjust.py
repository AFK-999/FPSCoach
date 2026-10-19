from copy import deepcopy

from fps_coach.curve_adjust import (
    adjust_from_recommendation,
    apply_preset,
    normalized_overlay,
    preset_labels,
)
from fps_coach.curve_io import CurvePoint, CurveProfile


def _base() -> CurveProfile:
    pts = [
        CurvePoint(0.0, 1.0),
        CurvePoint(25.0, 1.0),
        CurvePoint(50.0, 1.0),
        CurvePoint(100.0, 1.0),
    ]
    return CurveProfile(name="MyCurve", horizontal=pts, vertical=deepcopy(pts), source_format="cc4")


def test_adjust_scales_by_sensitivity_and_keeps_speeds() -> None:
    base = _base()
    rec = {"points": [(0.0, 0.0), (0.5, 0.6), (1.0, 1.0)], "sensitivity_multiplier": 1.2}
    adjusted, note = adjust_from_recommendation(base, rec)
    # Speed points are preserved (we adjust, not regenerate).
    assert [p.speed for p in adjusted.horizontal] == [p.speed for p in base.horizontal]
    # Base multipliers were all 1.0; after a 1.2x sensitivity they should rise.
    assert adjusted.horizontal[1].multiplier > 1.0
    # The base itself is never mutated.
    assert base.horizontal[1].multiplier == 1.0
    assert "imported curve" in note.lower()


def test_fast_flicks_boosts_high_speed_more_than_low() -> None:
    base = _base()
    adjusted, _ = apply_preset(base, "fast_flicks")
    low = adjusted.horizontal[1].multiplier
    high = adjusted.horizontal[-1].multiplier
    assert high > low  # more boost at high speed
    assert base.horizontal[-1].multiplier == 1.0  # base untouched


def test_raise_and_lower_sensitivity() -> None:
    base = _base()
    up, _ = apply_preset(base, "raise_sens")
    down, _ = apply_preset(base, "lower_sens")
    assert up.horizontal[0].multiplier > 1.0
    assert down.horizontal[0].multiplier < 1.0


def test_reset_returns_base_values() -> None:
    base = _base()
    reset, _ = apply_preset(base, "reset")
    assert [p.multiplier for p in reset.horizontal] == [p.multiplier for p in base.horizontal]
    assert reset.name == base.name


def test_preset_labels_nonempty_and_known_keys() -> None:
    labels = preset_labels()
    keys = {k for k, _ in labels}
    assert {"fast_flicks", "precision", "tracking", "reset"} <= keys
    assert all(label for _, label in labels)


def test_normalized_overlay_shared_scale_shows_scaling() -> None:
    base = _base()
    bigger, _ = apply_preset(base, "raise_sens")
    prev_pts, cur_pts = normalized_overlay(base, bigger)
    # Same number of points, and the adjusted (raised) curve sits above the base
    # at a shared normalized scale.
    assert len(prev_pts) == len(cur_pts) == len(base.horizontal)
    assert cur_pts[1][1] >= prev_pts[1][1]
