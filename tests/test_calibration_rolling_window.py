"""Recent-trial metrics and speed samples drive the live recommendation window."""
from __future__ import annotations

import math

import pytest
from fps_coach.backend import (
    CALIBRATION_WINDOW_TRIALS,
    CURVE_DISTRIBUTION_VERSION,
    CoreBackend,
    _FallbackAnalyzer,
)

METRIC_KEYS = ("overshoot_score", "underreach_score", "correction_score", "tremor_score")


def add_trial(analyzer, kind: str, offset: float):
    analyzer.begin_trial(0, 0, 100, 0, offset)
    points = {
        "overshoot": [(20, 0), (65, 7), (125, 1), (110, -4)],
        "underreach": [(10, 0), (25, 3), (55, -2), (70, 0)],
        "straight": [(15, 0), (45, 0), (75, 0)],
        "wavy": [(10, 4), (35, -5), (55, 6), (80, -3)],
    }[kind]
    for i, (x, y) in enumerate(points, 1):
        analyzer.add_sample(x, y, offset + i * 0.01)
    endpoint = 75 if kind == "underreach" else 100
    assert analyzer.end_trial(endpoint, 0, offset + (len(points) + 1) * 0.01)


def test_window_mean_evicts_old_trials_and_drives_formula_and_graph():
    a = _FallbackAnalyzer()
    samples = ["overshoot"] * 4 + ["wavy", "straight", "underreach", "overshoot", "straight"]
    individual = []
    for index, kind in enumerate(samples):
        add_trial(a, kind, index * 2.0)
        one = _FallbackAnalyzer()
        add_trial(one, kind, index * 2.0)
        individual.append(one.summary())
        summary = a.summary()
        chosen = individual[-CALIBRATION_WINDOW_TRIALS:]
        assert summary["trial_count"] == index + 1
        assert summary["window_trial_count"] == len(chosen)
        assert summary["window_capacity"] == CALIBRATION_WINDOW_TRIALS
        for key in METRIC_KEYS:
            assert summary[key] == pytest.approx(sum(row[key] for row in chosen) / len(chosen))
        recommendation = a.recommend()
        expected = min(1.12, max(0.82, 1 - .12 * summary["overshoot_score"]
                                     - .05 * summary["correction_score"]
                                     + .10 * summary["underreach_score"]))
        assert recommendation["sensitivity_multiplier"] == pytest.approx(expected)

    fresh = _FallbackAnalyzer()
    for index, kind in enumerate(samples[-CALIBRATION_WINDOW_TRIALS:], 1):
        add_trial(fresh, kind, index * 2.0)
    assert a.recommend()["captured_sample_count"] == fresh.recommend()["captured_sample_count"]
    assert a.recommend()["points"] == pytest.approx(fresh.recommend()["points"])
    assert a.recommend()["sensitivity_multiplier"] == pytest.approx(fresh.recommend()["sensitivity_multiplier"])


def test_newer_trials_replace_old_behavior_and_change_sensitivity():
    a = _FallbackAnalyzer()
    for i in range(CALIBRATION_WINDOW_TRIALS):
        add_trial(a, "overshoot", i * 2.0)
    before = a.recommend()["sensitivity_multiplier"]
    for i in range(CALIBRATION_WINDOW_TRIALS):
        add_trial(a, "underreach", 30 + i * 2.0)
    summary = a.summary()
    assert summary["trial_count"] == 16
    assert summary["window_trial_count"] == 8
    assert summary["overshoot_score"] == pytest.approx(0)
    assert summary["underreach_score"] > 0
    assert a.recommend()["sensitivity_multiplier"] > before


def test_rejected_trials_do_not_evict_and_reset_clears_all_state():
    a = _FallbackAnalyzer()
    add_trial(a, "overshoot", 0)
    before_summary, before_rec = a.summary(), a.recommend()
    a.begin_trial(0, 0, 100, 0, 20)
    assert not a.end_trial(0, 0, 21)
    assert a.summary() == before_summary
    assert a.recommend() == before_rec
    a.reset()
    assert a.summary()["trial_count"] == 0
    assert a.summary()["window_trial_count"] == 0
    assert a.recommend()["points"] == []
    assert a.recommend()["captured_sample_count"] == 0
    assert math.isfinite(a.recommend()["confidence"])


def test_old_native_analyzer_falls_back_without_disabling_native_capture(monkeypatch):
    class OldAnalyzer:
        def summary(self):
            return {"trial_count": 0}

    class Native:
        MovementAnalyzer = OldAnalyzer
        DesktopCapture = object

    monkeypatch.setattr("fps_coach.backend._native", Native)
    core = CoreBackend()
    assert core.native_available
    assert core.uses_python_analyzer
    assert isinstance(core.analyzer, _FallbackAnalyzer)
    assert core.core_label == "Native capture, Python analyzer"


def test_matching_native_analyzer_is_used(monkeypatch):
    class NewAnalyzer:
        def summary(self):
            return {"trial_count": 0, "window_trial_count": 0, "window_capacity": CALIBRATION_WINDOW_TRIALS,
                    "curve_distribution_version": CURVE_DISTRIBUTION_VERSION}

    class Native:
        MovementAnalyzer = NewAnalyzer
        DesktopCapture = object

    monkeypatch.setattr("fps_coach.backend._native", Native)
    core = CoreBackend()
    assert not core.uses_python_analyzer
    assert isinstance(core.analyzer, NewAnalyzer)


def test_coach_context_names_recent_window():
    from fps_coach.ai_chat import curve_context

    summary = {"trial_count": 12, "window_trial_count": 8, "window_capacity": 8,
               "overshoot_score": 0.15}
    context = curve_context(summary, {})
    assert "Trials: 12" in context
    assert "latest 8 accepted trials" in context
    assert "Overshoot score: 0.150" in context
