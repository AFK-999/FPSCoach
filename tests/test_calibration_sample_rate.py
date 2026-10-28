"""Calibration cursor sampling targets high-polling mice without repainting at 1,000 FPS.

The selected rate controls a precise Qt timer; every mouse-move event is also recorded. The UI calls
this a *target* rate because Windows/Qt scheduling can make the achieved cadence lower under load.
The analyzer must retain enough samples for a realistic 1 kHz trial in both native and Python modes.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fps_coach.ui.calibration import (  # noqa: E402
    CALIBRATION_SAMPLE_RATES_HZ,
    DEFAULT_CALIBRATION_SAMPLE_RATE_HZ,
    CalibrationCanvas,
)

_KEEP_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class FakeAnalyzer:
    def __init__(self) -> None:
        self.samples: list[tuple[float, float, float]] = []
        self.active = False

    def reset(self) -> None:
        self.samples.clear()
        self.active = False

    def begin_trial(self, *args) -> None:
        self.active = True

    def add_sample(self, x: float, y: float, timestamp: float) -> None:
        self.samples.append((x, y, timestamp))

    def end_trial(self, *args) -> bool:
        return True

    def summary(self) -> dict:
        return {"trial_count": 12}

    def recommend(self) -> dict:
        return {
            "points": [(0.0, 0.0), (0.5, 0.4), (1.0, 1.0)],
            "captured_sample_count": len(self.samples),
            "sensitivity_multiplier": 1.0,
        }


def test_canvas_defaults_to_1000_hz_and_uses_a_one_millisecond_precise_timer(qapp) -> None:
    analyzer = FakeAnalyzer()
    canvas = CalibrationCanvas(analyzer)
    _KEEP_ALIVE.append(canvas)
    assert canvas.sample_rate_hz == DEFAULT_CALIBRATION_SAMPLE_RATE_HZ == 1000
    assert canvas._sample_timer.interval() == 1
    assert canvas._sample_timer.timerType().name == "PreciseTimer"
    assert canvas._trail.maxlen == 720  # keep about the same visible trail duration as 125 Hz


def test_supported_rates_map_to_exact_timer_intervals(qapp) -> None:
    canvas = CalibrationCanvas(FakeAnalyzer())
    _KEEP_ALIVE.append(canvas)
    expected_ms = {125: 8, 250: 4, 500: 2, 1000: 1}
    assert CALIBRATION_SAMPLE_RATES_HZ == tuple(expected_ms)
    for rate, interval in expected_ms.items():
        canvas.set_sample_rate(rate)
        assert canvas.sample_rate_hz == rate
        assert canvas._sample_timer.interval() == interval
        assert canvas._trail.maxlen == round(rate * 0.72)


def test_unsupported_rates_are_rejected_without_changing_the_current_timer(qapp) -> None:
    canvas = CalibrationCanvas(FakeAnalyzer())
    _KEEP_ALIVE.append(canvas)
    with pytest.raises(ValueError, match="125 and 1000"):
        canvas.set_sample_rate(1001)
    assert canvas.sample_rate_hz == 1000
    assert canvas._sample_timer.interval() == 1


def test_changing_rate_during_a_session_keeps_sampling_active(qapp) -> None:
    analyzer = FakeAnalyzer()
    canvas = CalibrationCanvas(analyzer)
    canvas.resize(600, 380)
    _KEEP_ALIVE.append(canvas)
    canvas.start_session(3)
    assert canvas._sample_timer.isActive()

    canvas.set_sample_rate(500)

    assert canvas._sample_timer.isActive()
    assert canvas._sample_timer.interval() == 2
    canvas.cancel_session()
    assert not canvas._sample_timer.isActive()


def test_canvas_updates_the_curve_after_each_completed_target(qapp) -> None:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    canvas = CalibrationCanvas(FakeAnalyzer())
    canvas.resize(600, 380)
    canvas.show()
    _KEEP_ALIVE.append(canvas)
    updates: list[tuple[dict, dict]] = []
    completed: list[tuple[dict, dict]] = []
    canvas.recommendation_updated.connect(lambda summary, rec: updates.append((summary, rec)))
    canvas.completed.connect(lambda summary, rec: completed.append((summary, rec)))
    canvas.start_session(3)
    qapp.processEvents()

    for _ in range(3):
        local = QPointF(canvas._target)
        global_point = canvas.mapToGlobal(QPoint(round(local.x()), round(local.y())))
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local,
            QPointF(global_point),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mousePressEvent(event)

    assert len(updates) == 2  # the finished recommendation is sent once, via completed
    assert all(len(rec["points"]) == 3 for _summary, rec in updates)
    assert len(completed) == 1
    assert not canvas._sample_timer.isActive()


def test_calibration_page_exposes_and_persists_the_target_rate(qapp, monkeypatch) -> None:
    from fps_coach import backend
    from fps_coach.ui import pages_setup

    class Settings:
        def __init__(self) -> None:
            self.values = {"calibration/sample_hz": 500}

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    settings = Settings()
    monkeypatch.setattr(pages_setup, "_settings", lambda: settings)
    page = pages_setup.CalibrationPage(SimpleNamespace(analyzer=backend._FallbackAnalyzer()))
    _KEEP_ALIVE.append(page)

    offered = [page.sample_rate_combo.itemData(i) for i in range(page.sample_rate_combo.count())]
    assert offered == [125, 250, 500, 1000]
    assert page.sample_rate_combo.currentData() == 500
    assert page.canvas.sample_rate_hz == 500
    assert page.canvas._sample_timer.interval() == 2

    page.sample_rate_combo.setCurrentIndex(page.sample_rate_combo.findData(1000))

    assert page.canvas.sample_rate_hz == 1000
    assert page.canvas._sample_timer.interval() == 1
    assert settings.values["calibration/sample_hz"] == 1000
    assert "scheduling" in page.sample_rate_combo.toolTip().lower()


def test_invalid_saved_rate_falls_back_to_1000_hz(qapp, monkeypatch) -> None:
    from fps_coach.ui import pages_setup

    class Settings:
        def value(self, key, default=None):
            return "not a rate"

        def setValue(self, key, value):
            raise AssertionError("the constructor should not rewrite an invalid value")

    monkeypatch.setattr(pages_setup, "_settings", Settings)
    page = pages_setup.CalibrationPage(SimpleNamespace(analyzer=None))
    _KEEP_ALIVE.append(page)
    assert page.sample_rate_combo.currentData() == 1000
    assert page.canvas.sample_rate_hz == 1000


def test_sampling_at_1000_hz_does_not_repaint_at_1000_fps(qapp, monkeypatch) -> None:
    from fps_coach.ui import calibration
    from PySide6.QtCore import QPointF

    analyzer = FakeAnalyzer()
    canvas = CalibrationCanvas(analyzer)
    _KEEP_ALIVE.append(canvas)
    canvas._active = True
    canvas._last_repaint_at = 0.0
    cursor_positions = iter((QPointF(10.0, 10.0), QPointF(11.0, 10.0)))
    times = iter((1.0, 1.0, 1.001, 1.001))
    repaints: list[None] = []
    monkeypatch.setattr(canvas, "_cursor_global", lambda: next(cursor_positions))
    monkeypatch.setattr(canvas, "mapFromGlobal", lambda point: point)
    monkeypatch.setattr(canvas, "update", lambda *args: repaints.append(None))
    monkeypatch.setattr(calibration.time, "perf_counter", lambda: next(times))

    canvas._sample_cursor()
    canvas._sample_cursor()

    assert len(analyzer.samples) == 2
    assert len(repaints) == 1


def test_python_fallback_keeps_a_full_12_second_1000_hz_trial() -> None:
    from fps_coach.backend import MAX_ANALYSIS_SAMPLES_PER_TRIAL, _FallbackAnalyzer

    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0.0, 0.0, 100.0, 0.0, 1.0)
    for i in range(1, 12_001):
        x = 100.0 * i / 12_000
        y = 5.0 if i % 2 == 0 else -5.0
        analyzer.add_sample(x, y, 1.0 + i / 1000.0)
    assert analyzer.end_trial(100.0, 0.0, 13.1)
    assert MAX_ANALYSIS_SAMPLES_PER_TRIAL >= 12_001
    assert analyzer.summary()["mean_speed_px_s"] > 9_000


def test_fallback_click_endpoint_does_not_exceed_sample_cap(monkeypatch) -> None:
    from fps_coach import backend

    monkeypatch.setattr(backend, "MAX_ANALYSIS_SAMPLES_PER_TRIAL", 3)
    analyzer = backend._FallbackAnalyzer()
    analyzer.begin_trial(0.0, 0.0, 100.0, 0.0, 1.0)
    analyzer.add_sample(30.0, 0.0, 2.0)
    analyzer.add_sample(60.0, 0.0, 3.0)
    analyzer.add_sample(90.0, 0.0, 4.0)  # sampling stays bounded after the cap
    samples = analyzer._trial["samples"]
    assert len(samples) == 3

    assert analyzer.end_trial(100.0, 0.0, 5.0)

    assert len(samples) == 3
    assert (samples[-1].x, samples[-1].t) == (100.0, 5.0)
