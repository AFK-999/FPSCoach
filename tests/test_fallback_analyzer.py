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


def _add_variable_speed_trial(analyzer: _FallbackAnalyzer, segments: int) -> None:
    analyzer.begin_trial(0.0, 0.0, 100.0, 0.0, 1.0)
    for index in range(1, segments + 1):
        fraction = index / (segments + 1)
        analyzer.add_sample(100.0 * fraction**2, 0.0, 1.0 + index * 0.01)
    assert analyzer.end_trial(100.0, 0.0, 1.0 + (segments + 1) * 0.01)


def test_recommendation_points_adapt_to_captured_speed_samples() -> None:
    sparse = _FallbackAnalyzer()
    dense = _FallbackAnalyzer()
    _add_variable_speed_trial(sparse, 2)
    _add_variable_speed_trial(dense, 24)

    sparse_recommendation = sparse.recommend()
    dense_recommendation = dense.recommend()
    sparse_points = sparse_recommendation["points"]
    dense_points = dense_recommendation["points"]

    assert sparse_recommendation["captured_sample_count"] == 3
    assert dense_recommendation["captured_sample_count"] == 25
    assert 2 <= len(sparse_points) < len(dense_points) <= 32
    assert sparse_points[0] == (0.0, 0.0)
    assert dense_points[-1] == (1.0, 1.0)
    for points in (sparse_points, dense_points):
        assert all(points[i][0] < points[i + 1][0] for i in range(len(points) - 1))
        assert all(points[i][1] <= points[i + 1][1] for i in range(len(points) - 1))


def test_no_captured_movement_does_not_return_a_preset_curve() -> None:
    recommendation = _FallbackAnalyzer().recommend()
    assert recommendation["points"] == []
    assert recommendation["captured_sample_count"] == 0
