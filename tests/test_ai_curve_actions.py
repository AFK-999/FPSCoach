from copy import deepcopy

from fps_coach.ai_curve_actions import apply_adjustment, parse_adjustment
from fps_coach.curve_io import CurvePoint, CurveProfile


def _base() -> CurveProfile:
    pts = [CurvePoint(0.0, 1.0), CurvePoint(50.0, 1.0), CurvePoint(100.0, 1.0)]
    return CurveProfile(name="Base", horizontal=pts, vertical=deepcopy(pts), source_format="cc4")


def test_directive_preset() -> None:
    plan = parse_adjustment("Here is my advice.\n[ADJUST: fast_flicks]")
    assert plan == {"kind": "preset", "key": "fast_flicks"}


def test_directive_sensitivity_percent() -> None:
    plan = parse_adjustment("Try a touch more speed. [ADJUST: sensitivity +10%]")
    assert plan is not None and plan["kind"] == "sensitivity"
    assert abs(plan["factor"] - 1.10) < 1e-6


def test_directive_sensitivity_multiplier() -> None:
    plan = parse_adjustment("[ADJUST: sensitivity x0.95]")
    assert plan == {"kind": "sensitivity", "factor": 0.95}


def test_plain_explanation_does_not_trigger() -> None:
    # Mentions 'flick' but is not recommending a change -> no adjustment.
    assert parse_adjustment("A flick is a fast snap to the target.") is None


def test_free_text_actionable_fallback() -> None:
    plan = parse_adjustment("I recommend you optimize for flicks here.")
    assert plan == {"kind": "preset", "key": "fast_flicks"}


def test_free_text_increase_sensitivity() -> None:
    plan = parse_adjustment("Let's increase your sensitivity by 5%.")
    assert plan is not None and plan["kind"] == "sensitivity"
    assert abs(plan["factor"] - 1.05) < 1e-6


def test_apply_adjustment_preset_and_sensitivity() -> None:
    base = _base()
    adjusted, note = apply_adjustment(base, {"kind": "preset", "key": "fast_flicks"})
    assert adjusted.horizontal[-1].multiplier > base.horizontal[-1].multiplier
    assert note

    scaled, note2 = apply_adjustment(base, {"kind": "sensitivity", "factor": 1.2})
    assert abs(scaled.horizontal[0].multiplier - 1.2) < 1e-6
    assert base.horizontal[0].multiplier == 1.0  # base untouched
