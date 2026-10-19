from fps_coach.calibration_history import (
    CalibrationHistory,
    CalibrationRecord,
    compute_score,
)


def _summary(path_eff=0.9, overshoot=0.1, correction=0.1, tremor=0.05, under=0.05, trials=12):
    return {
        "path_efficiency": path_eff,
        "overshoot_score": overshoot,
        "correction_score": correction,
        "tremor_score": tremor,
        "underreach_score": under,
        "trial_count": trials,
    }


def test_compute_score_bounds_and_ordering() -> None:
    good = compute_score(_summary(path_eff=1.0, overshoot=0.0, correction=0.0, tremor=0.0, under=0.0))
    bad = compute_score(_summary(path_eff=0.3, overshoot=0.9, correction=0.8, tremor=0.7, under=0.6))
    assert 0.0 <= bad < good <= 100.0
    assert good == 100.0


def test_history_keeps_previous_and_reports_latest(tmp_path) -> None:
    path = tmp_path / "hist.json"
    history = CalibrationHistory.load(path)
    assert history.latest() is None and history.previous() is None

    first = history.add(_summary(path_eff=0.6))
    assert history.latest() is first
    assert history.previous() is None  # only one so far

    second = history.add(_summary(path_eff=0.95))
    assert history.latest() is second
    assert history.previous() is first  # previous NOT overwritten
    assert second.score != first.score


def test_history_persists_across_reload(tmp_path) -> None:
    path = tmp_path / "hist.json"
    h1 = CalibrationHistory.load(path)
    h1.add(_summary(path_eff=0.7))
    h1.add(_summary(path_eff=0.8))

    h2 = CalibrationHistory.load(path)
    assert len(h2.records) == 2
    assert h2.latest().score == h1.latest().score
    assert h2.previous().score == h1.previous().score


def test_recent_is_most_recent_first(tmp_path) -> None:
    history = CalibrationHistory.load(tmp_path / "h.json")
    for eff in (0.5, 0.6, 0.7):
        history.add(_summary(path_eff=eff))
    recent = history.recent(2)
    assert len(recent) == 2
    assert recent[0].score >= 0 and isinstance(recent[0], CalibrationRecord)
    # Most recent (eff 0.7) should come first.
    assert recent[0].path_efficiency == 0.7
