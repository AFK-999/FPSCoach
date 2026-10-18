from fps_coach.backend import _FallbackAnalyzer


def add_trial(analyzer: _FallbackAnalyzer, points: list[tuple[float, float]], offset: float) -> None:
    analyzer.begin_trial(0, 0, 100, 0, offset)
    for index, (x, y) in enumerate(points, 1):
        analyzer.add_sample(x, y, offset + index * 0.01)
    assert analyzer.end_trial(100, 0, offset + (len(points) + 1) * 0.01)


def test_straight_trials_produce_monotonic_curve() -> None:
    analyzer = _FallbackAnalyzer()
    for trial in range(12):
        add_trial(analyzer, [(20, 0), (40, 0), (60, 0), (80, 0)], float(trial))

    summary = analyzer.summary()
    result = analyzer.recommend()

    assert summary["trial_count"] == 12
    assert summary["path_efficiency"] == 1.0
    assert result["confidence"] > 0.7
    ys = [point[1] for point in result["points"]]
    assert ys == sorted(ys)
    assert result["points"][-1] == (1.0, 1.0)


def test_overshoot_reduces_sensitivity() -> None:
    baseline = _FallbackAnalyzer()
    overshoot = _FallbackAnalyzer()
    for trial in range(8):
        add_trial(baseline, [(25, 0), (50, 0), (75, 0)], float(trial))
        add_trial(overshoot, [(50, 0), (125, 2), (112, -2)], float(trial))

    assert (
        overshoot.recommend()["sensitivity_multiplier"]
        < baseline.recommend()["sensitivity_multiplier"]
    )
