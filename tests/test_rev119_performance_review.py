"""Coach disclosure and real task-to-model calibration telemetry regressions."""
from __future__ import annotations

import json
import time
from copy import deepcopy

import pytest
from fps_coach.backend import _FallbackAnalyzer
from fps_coach.curve_io import CurvePoint, CurveProfile, recommendation_profile
from fps_coach.dynamic_curve_ai import build_messages
from fps_coach.performance_telemetry import PerformanceTelemetry
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=3000):
    end = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < end:
        QTest.qWait(15)
    assert predicate()


def mouse(canvas, kind, local):
    global_pos = QPointF(canvas.mapToGlobal(local.toPoint()))
    btn = Qt.MouseButton.LeftButton if kind == QMouseEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
    return QMouseEvent(kind, local, global_pos, btn, btn, Qt.KeyboardModifier.NoModifier)


def test_telemetry_preserves_small_movement_and_rejects_invalid_order():
    t = PerformanceTelemetry("calibration")
    t.begin_target(10, 0, 0)
    t.move(.03, 0, 10.1)
    t.move(.06, 0, 10.2)
    assert t.click(.06, 0, .06, 0, 2, 10.15) is None
    s = t.click(.06, 0, .06, 0, 2, 10.3)
    attempt = s["recent_attempts"][0]
    assert attempt["observed_path_px"] == pytest.approx(.06)
    assert attempt["movement_samples"] == 2
    assert attempt["path_efficiency"] == pytest.approx(1)
    assert t.click(1, 0, 0, 0, -1, 10.4) is None
    t.break_path()
    t.move(1000, 0, 10.5)
    s = t.click(1001, 0, 1001, 0, 2, 10.6)
    assert s["recent_attempts"][-1]["observed_path_px"] == pytest.approx(1)
    assert s["recent_attempts"][-1]["click_ms"] == pytest.approx(600)


def test_only_matching_generated_calibration_reference_maps_average_speed():
    base = CurveProfile("base", [CurvePoint(0, 1), CurvePoint(100, 1)],
                        [CurvePoint(0, 1), CurvePoint(100, 1)])
    t = PerformanceTelemetry("calibration")
    t.begin_target(1, 0, 0)
    t.move(25, 0, 1.25)
    c = t.click(50, 0, 50, 0, 10, 1.5)
    c["input_path"] = "raw"
    metrics = {"calibration": c, "_curve_mapping": {"input_path": "raw", "reference_px_s": 200.0}}
    payload = json.loads(build_messages({"accuracy_pct": 90, "click_ms": 600}, metrics, base)[1]["content"])
    source = payload["sources_kept_separate"][0]
    assert source["calibration_curve_speed_reference_px_s"] == 200
    assert source["recent_attempts"][0]["mean_speed_fraction_of_reference_clipped"] == pytest.approx(.5)
    assert source["recent_attempts"][0]["movement_samples"] == 2
    assert "approximations" in source["speed_mapping_note"]
    metrics["_curve_mapping"]["input_path"] = "desktop"
    source = json.loads(build_messages({"accuracy_pct": 90, "click_ms": 600}, metrics, base)[1]["content"])["sources_kept_separate"][0]
    assert "calibration_curve_speed_reference_px_s" not in source
    assert "mean_speed_fraction_of_reference_clipped" not in source["recent_attempts"][0]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [(900, 700), (1350, 900)])
def test_coach_disclosure_shared_accessible_and_keyboard_operable(qapp, monkeypatch, tmp_path, theme, size):
    from fps_coach.ui import dynamic_curve, main_window
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/theme", theme)
    monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
    monkeypatch.setattr(main_window, "_settings", lambda: settings)
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    win = main_window.MainWindow()
    _KEEP.append(win)
    win.resize(*size)
    win.show()
    win._select_page("recommendation")
    page = win.recommendation_page
    page.curve_deck._animations_enabled = lambda: False
    QTest.qWait(30)
    assert not hasattr(win.chat_page, "dynamic_curve_section")
    assert page.curve_deck.currentIndex() == 0
    page.dynamic_curve_btn.setFocus()
    QTest.keyClick(page.dynamic_curve_btn, Qt.Key.Key_Space)
    assert page.curve_deck.currentIndex() == 1
    panel = page.dynamic_curve_panel
    panel.enabled_box.setChecked(True)
    assert win.training_page.dynamic_curve_panel.enabled_box.isChecked()
    assert win.calibration_page.canvas._performance_enabled
    panel.accuracy.setValue(81)
    assert win.training_page.dynamic_curve_panel.accuracy.value() == 81
    page.dynamic_back_btn.click()
    assert page.curve_deck.currentIndex() == 0 and win.dynamic_curve.enabled
    win.dynamic_curve.set_enabled(False)
    win.close()


def test_calibration_reentry_uses_event_origin_and_rejects_outside_click(qapp, monkeypatch):
    from fps_coach.ui import calibration
    canvas = calibration.CalibrationCanvas(_FallbackAnalyzer())
    _KEEP.append(canvas)
    canvas.resize(600, 430)
    canvas.show()
    canvas._active = True
    canvas.set_performance_enabled(True)
    canvas._target = QPointF(200, 100)
    canvas._outside_arena = True
    # Simulate a stale OS cursor; a genuine re-entry event has authoritative coordinates.
    monkeypatch.setattr(canvas, "_cursor_global", lambda: QPointF(-900, -900))
    p = QPointF(120, 100)
    canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, p))
    assert canvas._performance._origin == (120, 100)
    assert canvas._analyzer._trial["samples"][0].x == canvas.mapToGlobal(p.toPoint()).x()
    emitted = []
    canvas.performance_updated.connect(emitted.append)
    canvas.mousePressEvent(mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(-20, 100)))
    assert not emitted and canvas._outside_arena
    canvas.cancel_session()
    canvas.close()


def test_training_mid_target_enable_and_stale_delayed_reveal(qapp, monkeypatch):
    from fps_coach import scenarios
    from fps_coach.ui import pages_practice
    canvas = pages_practice.TrainingCanvas()
    _KEEP.append(canvas)
    canvas.resize(600, 450)
    clock = [20.0]
    monkeypatch.setattr(pages_practice.time, "perf_counter", lambda: clock[0])
    canvas.start(scenarios.get("flick"))
    canvas._visible_target = True
    canvas._spawn_time = 1.0
    canvas._target = QPointF(200, 100)
    canvas.set_performance_enabled(True)
    assert canvas._performance._target_started == 20.0
    clock[0] = 20.25
    canvas.mousePressEvent(mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(200, 100)))
    assert canvas._performance.snapshot()["mean_hit_click_ms"] == pytest.approx(250)
    previous = canvas._target_generation
    canvas.stop()
    canvas.start(scenarios.get("flick"))
    current_time = canvas._spawn_time
    clock[0] = 30
    canvas._reveal_target(previous)
    assert canvas._spawn_time == current_time
    canvas.stop()
    canvas.close()


def test_stale_preview_is_cleared_when_evidence_disappears(qapp):
    from fps_coach.ui.dynamic_curve import DynamicCurveEngine
    base = CurveProfile("base", [CurvePoint(0, 1), CurvePoint(100, 1)],
                        [CurvePoint(0, 1), CurvePoint(100, 1)])
    engine = DynamicCurveEngine(lambda: base, lambda: True)
    _KEEP.append(engine)
    engine.set_enabled(True)
    engine.update_metrics({"source": "calibration", "window_attempts": 3, "window_hits": 2})
    engine.base = engine.proposed = base
    engine._fresh = True
    engine.session_ended("calibration", False)
    assert engine.base is None and engine.proposed is None and not engine.can_apply()
    assert "Waiting" in engine.status
    engine.set_enabled(False)


@pytest.mark.parametrize("mode", ["desktop", "raw"])
def test_calibration_events_reach_local_ai_mapping_preview_and_confirmation(qapp, tmp_path, monkeypatch, mode):
    from fps_coach import dynamic_curve_ai
    from fps_coach.ui import dynamic_curve, main_window, raw_mouse_capture
    from test_raw_calibration import FakeAPI, _packet

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("ai/provider", "ollama")
    settings.setValue("ai/base_url", "http://localhost:11434")
    settings.setValue("ai/model", "model")
    monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
    monkeypatch.setattr(main_window, "_settings", lambda: settings)
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    calls = []
    def fake_ollama(_opener, url, payload, timeout):
        if url.endswith("/tags"):
            return {"models": [{"name": "model:latest"}]}
        if url.endswith("/show"):
            return {"model_info": {}}
        data = json.loads(payload["messages"][1]["content"])
        calls.append(data)
        source = data["sources_kept_separate"][0]
        middle = 0.94 if source["window_accuracy_pct"] < data["goals"]["accuracy_pct"] else 1.04
        return {"message": {"content": json.dumps({"gain_factors": [
            {"speed_fraction": 0, "factor": 1}, {"speed_fraction": .5, "factor": middle},
            {"speed_fraction": 1, "factor": 1}], "reason": "Observed accuracy differs from goal."})}}
    monkeypatch.setattr(dynamic_curve_ai, "_json_request", fake_ollama)
    api = FakeAPI()
    cls = raw_mouse_capture.RawMouseCapture
    if mode == "raw":
        monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture", lambda arena, move, click, lost, **kw:
                            cls(arena, move, click, lost, api=api, **kw))
    win = main_window.MainWindow()
    _KEEP.append(win)
    win.resize(1350, 900)
    win.show()
    win._select_page("calibration")
    win.dynamic_curve.set_enabled(True)
    canvas = win.calibration_page.canvas
    canvas.set_input_mode(mode)
    QTest.qWait(50)
    canvas.start_session(3)
    assert canvas._active
    for trial in range(3):
        if mode == "raw":
            # Avoid an arena edge in the test; start each target at a known virtual point.
            canvas._raw_pos = QPointF(canvas.width() / 2, canvas.height() / 2)
            canvas._target = canvas._raw_pos + QPointF(60, 0)
            canvas._begin_trial()
            _packet(canvas, api, dx=10)
            _packet(canvas, api, dx=10, button=1)  # visible miss, outside acceptance radius
            _packet(canvas, api, dx=20)
            _packet(canvas, api, dx=20, button=1)
        else:
            origin = QPointF(120, 120)
            canvas._target = QPointF(180, 120)
            canvas._outside_arena = True
            canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, origin))
            canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(130, 120)))
            canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(140, 120)))
            canvas.mousePressEvent(mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(140, 120)))
            canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(160, 120)))
            canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(180, 120)))
            canvas.mousePressEvent(mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(180, 120)))
        assert canvas._completed_trials == trial + 1
        if trial == 1:
            win.dynamic_curve._dispatch()
            wait_until(lambda: bool(calls))
            wait_until(lambda: not win.dynamic_curve._running)
            assert not win.dynamic_curve.can_apply()  # active calibration locks confirmation
    assert not canvas._active
    data = win.dynamic_curve.metrics["calibration"]
    assert data["input_path"] == mode and data["window_attempts"] == 6
    assert data["window_hits"] == 3 and data["window_accuracy_pct"] == 50
    assert all(a["movement_samples"] >= 2 for a in data["recent_attempts"])
    base = deepcopy(win.calibration_page._profile)
    expected_ref = win._last_recommendation["speed_reference_px_s"]
    assert expected_ref > 0 and win._dynamic_speed_mapping()["reference_px_s"] == expected_ref
    win.dynamic_curve._dispatch()
    wait_until(win.dynamic_curve.can_apply)
    latest = calls[-1]["sources_kept_separate"][0]
    assert latest["window_attempts"] == 6 and latest["window_hits"] == 3
    assert latest["calibration_curve_speed_reference_px_s"] == expected_ref
    assert latest["recent_attempts"][-1]["mean_speed_fraction_of_reference_clipped"] >= 0
    assert win.calibration_page._profile == base
    assert win.recommendation_page.dynamic_curve_panel.chart.points
    from fps_coach.dynamic_curve_ai import _interpolate
    def gain(profile, speed):
        return _interpolate([(p.speed, p.multiplier) for p in profile.horizontal], speed)
    midpoint = base.horizontal[-1].speed * .5
    assert gain(win.dynamic_curve.proposed, midpoint) == pytest.approx(gain(base, midpoint) * .94)
    # A different goal is sent to the fake model and produces a different numerical preview.
    win.dynamic_curve.set_goals(45, 600)
    win.dynamic_curve._dispatch()
    wait_until(win.dynamic_curve.can_apply)
    assert calls[-1]["goals"]["accuracy_pct"] == 45
    assert gain(win.dynamic_curve.proposed, midpoint) == pytest.approx(gain(base, midpoint) * 1.04)
    assert win.calibration_page._profile == base
    win.recommendation_page.dynamic_curve_panel.confirm_btn.click()
    assert win.calibration_page._profile != base and win.stack.currentWidget() is win.calibration_page
    assert win._dynamic_speed_mapping() is None  # edited/confirmed base cannot claim calibration mapping
    win.dynamic_curve.set_enabled(False)
    win.close()


def test_imported_or_edited_profile_has_no_invented_speed_mapping(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import main_window
    monkeypatch.setattr(main_window, "_settings", lambda: QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    win = main_window.MainWindow()
    _KEEP.append(win)
    r = {"points": [(0, 0), (1, 1)], "sensitivity_multiplier": 1, "speed_reference_px_s": 500}
    win._last_recommendation = r
    win._last_summary = {"input_path": "desktop"}
    win.calibration_page._profile = recommendation_profile(r)
    assert win._dynamic_speed_mapping() == {"input_path": "desktop", "reference_px_s": 500.0}
    win.calibration_page._profile.horizontal[0].multiplier += .01
    assert win._dynamic_speed_mapping() is None
    win.calibration_page._profile = recommendation_profile(r)
    win.calibration_page._base_profile = deepcopy(win.calibration_page._profile)
    assert win._dynamic_speed_mapping() is None
    win.close()


def test_cancelling_calibration_clears_old_proposal_even_with_training_data(qapp):
    from fps_coach.ui.dynamic_curve import DynamicCurveEngine
    base = CurveProfile("base", [CurvePoint(0, 1), CurvePoint(100, 1)],
                        [CurvePoint(0, 1), CurvePoint(100, 1)])
    engine = DynamicCurveEngine(lambda: base, lambda: True)
    _KEEP.append(engine)
    engine.set_enabled(True)
    engine.update_metrics({"source": "training", "window_attempts": 3, "window_hits": 2})
    engine.update_metrics({"source": "calibration", "window_attempts": 3, "window_hits": 2})
    engine.base = engine.proposed = base
    engine._fresh = True
    engine.session_ended("calibration", False)
    assert "training" in engine.metrics and "calibration" not in engine.metrics
    assert engine.base is None and engine.proposed is None and not engine.can_apply()
    assert engine._timer.isActive()  # remaining training can generate a new proposal
    engine.set_enabled(False)


def test_training_ignores_outside_clicks_and_never_bridges_exit(qapp):
    from fps_coach import scenarios
    from fps_coach.ui.pages_practice import TrainingCanvas
    canvas = TrainingCanvas()
    _KEEP.append(canvas)
    canvas.resize(600, 450)
    canvas.set_performance_enabled(True)
    canvas.start(scenarios.get("flick"))
    canvas._visible_target = True
    canvas._target = QPointF(200, 100)
    canvas._performance.begin_target(time.perf_counter() - 1, 20, 100)
    canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(50, 100)))
    canvas.leaveEvent(None)
    canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(190, 100)))
    canvas.mousePressEvent(mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(-1, 100)))
    assert canvas._performance.total_attempts == 0
    canvas.mouseMoveEvent(mouse(canvas, QMouseEvent.Type.MouseMove, QPointF(190, 100)))
    canvas.mousePressEvent(mouse(canvas, QMouseEvent.Type.MouseButtonPress, QPointF(200, 100)))
    record = canvas._performance.snapshot()["recent_attempts"][-1]
    assert record["observed_path_px"] == pytest.approx(10)
    canvas.stop()
    canvas.close()


def test_delayed_training_reveal_timer_is_owned_cancelled_and_generation_safe(qapp):
    from types import SimpleNamespace

    from fps_coach.ui.pages_practice import TrainingCanvas
    canvas = TrainingCanvas()
    _KEEP.append(canvas)
    scenario = SimpleNamespace(target_radius=20, target_count=5, moving=False,
                               spawn_delay_ms=500, key="delayed")
    canvas.start(scenario)
    assert canvas._reveal_timer.parent() is canvas and canvas._reveal_timer.isActive()
    old = canvas._target_generation
    canvas.stop()
    assert not canvas._reveal_timer.isActive()
    canvas.start(scenario)
    assert canvas._target_generation > old and canvas._reveal_timer.isActive()
    canvas._reveal_target(old)
    assert not canvas._visible_target
    canvas.stop()
    canvas.close()
