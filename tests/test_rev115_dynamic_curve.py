"""Metric collection, model validation, async freshness and explicit curve confirmation."""
from __future__ import annotations

import json
import math
import threading
import time
from copy import deepcopy

import pytest
from fps_coach.curve_io import CurvePoint, CurveProfile
from fps_coach.dynamic_curve_ai import (
    analyze_performance,
    apply_factors,
    build_messages,
    parse_factors,
)
from fps_coach.guide_ai import GuideAIError
from fps_coach.performance_telemetry import PerformanceTelemetry
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

_KEEP = []


def wait_until(predicate, timeout=3000):
    deadline = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def base():
    return CurveProfile("base", [CurvePoint(0, 1), CurvePoint(100, 1)],
                        [CurvePoint(0, 1), CurvePoint(100, 1)])


def test_attempt_accuracy_times_speed_and_window_are_measured():
    t = PerformanceTelemetry("training", capacity=3)
    t.begin_target(10.0, 0, 0)
    t.move(50, 0, 10.1)
    miss = t.click(70, 0, 100, 0, 10, 10.2)
    assert miss["window_accuracy_pct"] == 0
    hit = t.click(100, 0, 100, 0, 10, 10.5)
    assert hit["window_accuracy_pct"] == 50
    assert hit["mean_hit_click_ms"] == pytest.approx(500)
    assert hit["mean_hit_speed_px_s"] == pytest.approx(200)
    assert hit["mean_hit_path_efficiency"] == pytest.approx(1)
    assert [x["hit"] for x in hit["recent_attempts"]] == [False, True]
    assert hit["recent_attempts"][0]["click_ms"] == pytest.approx(200)
    t.begin_target(12, 0, 0)
    t.click(10, 0, 10, 0, 1, 12.1)
    t.begin_target(13, 0, 0)
    last = t.click(10, 0, 10, 0, 1, 13.2)
    assert last["window_attempts"] == 3 and last["total_attempts"] == 4
    assert last["window_accuracy_pct"] == 100
    t.reset()
    assert t.snapshot()["window_accuracy_pct"] is None
    assert t.click(0, 0, 0, 0, 10, 15) is None


def test_prompt_includes_actual_clicks_goals_and_distinct_sources(base):
    cal = PerformanceTelemetry("calibration")
    cal.begin_target(1, 0, 0)
    cal.move(50, 0, 1.1)
    c = cal.click(100, 1, 100, 0, 10, 1.35)
    c["input_path"] = "raw"
    training = PerformanceTelemetry("training")
    training.begin_target(2, 0, 0)
    d = training.click(21, 0, 50, 0, 5, 2.2)
    d["input_path"] = "desktop"
    payload = json.loads(build_messages({"accuracy_pct": 82, "click_ms": 450},
                                        {"training": d, "calibration": c}, base)[1]["content"])
    assert payload["goals"] == {"accuracy_pct": 82, "hit_click_ms": 450}
    assert [source["source"] for source in payload["sources_kept_separate"]] == ["calibration", "training"]
    assert payload["sources_kept_separate"][0]["recent_attempts"][0]["click_ms"] == pytest.approx(350)
    assert payload["sources_kept_separate"][1]["recent_attempts"][0]["hit"] is False
    assert "not a named preset" in build_messages({"accuracy_pct": 82, "click_ms": 450}, {"training": d}, base)[0]["content"]


def valid_response():
    return {"gain_factors": [{"speed_fraction": 0, "factor": .94},
                             {"speed_fraction": .36, "factor": 1.04},
                             {"speed_fraction": 1, "factor": 1.08}],
            "reason": "Accuracy and timing suggest a small test."}


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(gain_factors=[]),
    lambda v: v["gain_factors"][1].update(factor=1.11),
    lambda v: v["gain_factors"][1].update(factor=float("nan")),
    lambda v: v["gain_factors"][1].update(factor=True),
    lambda v: v["gain_factors"][1].update(speed_fraction=0),
    lambda v: v["gain_factors"][0].update(speed_fraction=.01),
    lambda v: v.update(reason=""),
])
def test_rejects_malformed_unbounded_or_unordered_ai_output(mutate):
    response = valid_response()
    mutate(response)
    with pytest.raises(GuideAIError):
        parse_factors(json.dumps(response))


def test_ai_factors_create_nonpreset_curve_without_mutating_baseline(base):
    proposal = parse_factors(json.dumps(valid_response()))
    adjusted = apply_factors(base, proposal)
    assert [p.speed for p in adjusted.horizontal] == [0, 36, 100]
    assert [p.multiplier for p in adjusted.horizontal] == pytest.approx([.94, 1.04, 1.08])
    assert len(base.horizontal) == 2 and all(p.multiplier == 1 for p in base.horizontal)
    assert [p.multiplier for p in adjusted.vertical] == pytest.approx([.94, 1.04, 1.08])
    assert all(math.isfinite(p.multiplier) for p in adjusted.horizontal)


def test_only_verified_local_model_receives_payload(base, monkeypatch):
    from fps_coach import dynamic_curve_ai
    calls = []
    def request(_opener, url, payload, timeout):
        calls.append((url, payload))
        if url.endswith("/tags"):
            return {"models": [{"name": "model:latest"}]}
        if url.endswith("/show"):
            return {"model_info": {}}
        return {"message": {"content": json.dumps(valid_response())}}
    monkeypatch.setattr(dynamic_curve_ai, "_json_request", request)
    metrics = {"training": {"source": "training", "window_attempts": 3, "window_hits": 2,
                            "window_accuracy_pct": 66.6, "recent_attempts": []}}
    out = analyze_performance("ollama", "http://127.0.0.1:11434", "model", {"accuracy_pct": 90, "click_ms": 500}, metrics, base)
    assert len(out["anchors"]) == 3 and [url.rsplit("/", 1)[-1] for url, _ in calls] == ["tags", "show", "chat"]
    assert '"accuracy_pct": 90' in calls[-1][1]["messages"][1]["content"]
    calls.clear()
    with pytest.raises(GuideAIError):
        analyze_performance("openai", "https://example.com", "model", {"accuracy_pct": 90, "click_ms": 500}, metrics, base)
    assert not calls
    def remote(_opener, url, payload, timeout):
        if url.endswith("/tags"):
            return {"models": [{"name": "model:latest", "remote_host": "elsewhere"}]}
        raise AssertionError("Remote model must never receive metrics")
    monkeypatch.setattr(dynamic_curve_ai, "_json_request", remote)
    with pytest.raises(GuideAIError):
        analyze_performance("ollama", "http://localhost:11434", "model", {"accuracy_pct": 90, "click_ms": 500}, metrics, base)


def test_real_training_canvas_emits_measured_clicks_only_when_enabled(qapp, monkeypatch):
    from fps_coach import scenarios
    from fps_coach.ui import pages_practice
    canvas = pages_practice.TrainingCanvas()
    _KEEP.append(canvas)
    canvas.resize(600, 450)
    canvas.set_performance_enabled(True)
    clock = [100.0]
    monkeypatch.setattr(pages_practice.time, "perf_counter", lambda: clock[0])
    canvas.start(scenarios.get("flick"))
    canvas._target = QPointF(200, 150)
    canvas._visible_target = True
    canvas._spawn_time = 100.0
    canvas._performance.begin_target(100.0, 100, 150)
    clock[0] = 100.25
    event = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(200, 150), QPointF(200, 150),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    snapshots = []
    canvas.performance_updated.connect(snapshots.append)
    canvas.mousePressEvent(event)
    assert snapshots[0]["window_accuracy_pct"] == 100
    assert snapshots[0]["mean_hit_click_ms"] == pytest.approx(250)
    canvas.set_performance_enabled(False)
    canvas.mousePressEvent(event)
    assert len(snapshots) == 1
    canvas.stop()
    canvas.close()


def test_engine_coalesces_and_requires_current_data_before_confirmation(qapp, base, monkeypatch):
    from fps_coach.ui import dynamic_curve
    calls, confirmed = [], []
    started, release = threading.Event(), threading.Event()
    def analyze(_provider, _url, _model, goals, metrics, _base):
        calls.append((deepcopy(goals), deepcopy(metrics)))
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        return parse_factors(json.dumps(valid_response()))
    monkeypatch.setattr(dynamic_curve, "analyze_performance", analyze)
    engine = dynamic_curve.DynamicCurveEngine(lambda: base, lambda: True)
    _KEEP.append(engine)
    engine.confirmed.connect(lambda *args: confirmed.append(args))
    engine.set_enabled(True)
    snap = {"source": "training", "window_attempts": 3, "window_hits": 2, "window_accuracy_pct": 66.7}
    engine.update_metrics(snap)
    engine._dispatch()
    assert started.wait(2)
    snap["window_attempts"] = 4
    engine.update_metrics(snap)
    assert not engine.can_apply()
    release.set()
    wait_until(lambda: len(calls) == 2 and engine.can_apply())
    assert len(calls) == 2
    assert calls[-1][1]["training"]["window_attempts"] == 4
    assert engine.can_apply() and not confirmed
    engine.confirm()
    assert len(confirmed) == 1 and not engine.can_apply()
    assert len(base.horizontal) == 2
    engine.set_enabled(False)


def test_main_window_shares_controls_and_confirms_without_automatic_change(qapp, tmp_path, monkeypatch):
    from fps_coach.ui import dynamic_curve, main_window
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    monkeypatch.setattr(dynamic_curve, "analyze_performance", lambda *args: parse_factors(json.dumps(valid_response())))
    win = main_window.MainWindow()
    _KEEP.append(win)
    win.resize(1350, 920)
    win.show()
    a, b = win.recommendation_page.dynamic_curve_panel, win.training_page.dynamic_curve_panel
    a.enabled_box.setChecked(True)
    assert b.enabled_box.isChecked()
    assert win.calibration_page.canvas._performance_enabled and win.training_page.canvas._performance_enabled
    a.accuracy.setValue(82)
    assert b.accuracy.value() == 82
    assert win.calibration_page._profile is None
    win.training_page.canvas.performance_updated.emit({"source": "training", "input_path": "desktop",
        "window_attempts": 3, "window_hits": 2, "window_accuracy_pct": 66.7,
        "mean_hit_click_ms": 475, "mean_hit_speed_px_s": 350})
    win.dynamic_curve._dispatch()
    wait_until(win.dynamic_curve.can_apply)
    assert win.dynamic_curve.can_apply()
    assert win.calibration_page._profile is None  # display is never an application
    assert len(a.chart.points) == 3 and len(b.chart.points) == 3
    b.confirm_btn.click()
    assert win.stack.currentWidget() is win.calibration_page
    assert win.calibration_page._profile is not None
    assert len(win.calibration_page._profile.horizontal) == 3
    assert not win.dynamic_curve.can_apply()
    a.enabled_box.setChecked(False)
    assert not b.enabled_box.isChecked()
    assert not win.calibration_page.canvas._performance_enabled and not win.training_page.canvas._performance_enabled
    win.close()


def test_calibration_counts_visible_target_misses_independent_of_acceptance(qapp):
    import time

    from fps_coach.backend import _FallbackAnalyzer
    from fps_coach.ui.calibration import CalibrationCanvas
    canvas = CalibrationCanvas(_FallbackAnalyzer())
    _KEEP.append(canvas)
    canvas.resize(600, 450)
    canvas._active = True
    canvas.set_performance_enabled(True)
    canvas._target = QPointF(200, 150)
    canvas._performance.begin_target(time.perf_counter() - .5, 0, 0)
    snapshots = []
    canvas.performance_updated.connect(snapshots.append)
    canvas._accept_click(QPointF(250, 150), QPointF(250, 150))
    canvas._accept_click(QPointF(200, 150), QPointF(200, 150))
    assert [v["window_accuracy_pct"] for v in snapshots] == [0, 50]
    assert snapshots[-1]["input_path"] == "desktop"
    assert snapshots[-1]["mean_hit_click_ms"] >= 500
    canvas.cancel_session()
    canvas.close()


def test_unavailable_model_does_not_fall_back_to_a_heuristic(qapp, base, monkeypatch):
    from fps_coach.ui import dynamic_curve
    def fail(*_args):
        raise GuideAIError("Model not installed")
    monkeypatch.setattr(dynamic_curve, "analyze_performance", fail)
    engine = dynamic_curve.DynamicCurveEngine(lambda: base, lambda: True)
    _KEEP.append(engine)
    engine.set_enabled(True)
    engine.update_metrics({"source": "training", "window_attempts": 3, "window_hits": 1,
                           "window_accuracy_pct": 33.3})
    engine._dispatch()
    wait_until(lambda: "Model not installed" in engine.status)
    assert "Model not installed" in engine.status
    assert engine.proposed is None and not engine.can_apply()
    engine.set_enabled(False)


def test_disabling_discards_inflight_result_and_clears_telemetry(qapp, base, monkeypatch):
    from fps_coach.ui import dynamic_curve
    started, release = threading.Event(), threading.Event()
    def analyze(*_args):
        started.set()
        release.wait(2)
        return parse_factors(json.dumps(valid_response()))
    monkeypatch.setattr(dynamic_curve, "analyze_performance", analyze)
    engine = dynamic_curve.DynamicCurveEngine(lambda: base, lambda: True)
    _KEEP.append(engine)
    engine.set_enabled(True)
    engine.update_metrics({"source": "training", "window_attempts": 3, "window_hits": 1,
                           "window_accuracy_pct": 33.3})
    engine._dispatch()
    assert started.wait(2)
    engine.set_enabled(False)
    release.set()
    QTest.qWait(150)
    assert not engine.metrics and engine.proposed is None and not engine.can_apply()


def test_baseline_changed_after_preview_cannot_be_confirmed(qapp, base, monkeypatch):
    from fps_coach.ui import dynamic_curve
    monkeypatch.setattr(dynamic_curve, "analyze_performance", lambda *args: parse_factors(json.dumps(valid_response())))
    engine = dynamic_curve.DynamicCurveEngine(lambda: base, lambda: True)
    _KEEP.append(engine)
    confirmed = []
    engine.confirmed.connect(lambda *args: confirmed.append(args))
    engine.set_enabled(True)
    engine.update_metrics({"source": "training", "window_attempts": 3, "window_hits": 2,
                           "window_accuracy_pct": 66.7})
    engine._dispatch()
    wait_until(engine.can_apply)
    assert engine.can_apply()
    base.horizontal[0].multiplier = 1.03
    engine.confirm()
    assert not confirmed and not engine.can_apply()
    assert "Base curve changed" in engine.status
    engine.set_enabled(False)


def test_training_clicks_reach_local_model_and_return_to_live_preview(qapp, tmp_path, monkeypatch):
    from fps_coach import dynamic_curve_ai
    from fps_coach.ui import dynamic_curve, main_window

    settings = QSettings(str(tmp_path / "local.ini"), QSettings.Format.IniFormat)
    settings.setValue("ai/provider", "ollama")
    settings.setValue("ai/base_url", "http://localhost:11434")
    settings.setValue("ai/model", "model")
    monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    seen = []

    def fake_ollama(_opener, url, payload, timeout):
        if url.endswith("/tags"):
            return {"models": [{"name": "model:latest"}]}
        if url.endswith("/show"):
            return {"model_info": {}}
        values = json.loads(payload["messages"][1]["content"])
        seen.append(values)
        accuracy = values["sources_kept_separate"][0]["window_accuracy_pct"]
        # The fake model deliberately depends on the actual hit accuracy and goal.
        middle = 0.96 if accuracy < values["goals"]["accuracy_pct"] else 1.04
        response = valid_response()
        response["gain_factors"][1]["factor"] = middle
        return {"message": {"content": json.dumps(response)}}

    monkeypatch.setattr(dynamic_curve_ai, "_json_request", fake_ollama)
    win = main_window.MainWindow()
    _KEEP.append(win)
    win.show()
    win.dynamic_curve.set_enabled(True)
    win.dynamic_curve.set_goals(90, 500)
    canvas = win.training_page.canvas
    win.training_page._start()
    canvas._target = QPointF(200, 150)
    canvas._visible_target = True
    canvas._performance.begin_target(time.perf_counter() - .5, 100, 150)
    for point in (QPointF(230, 150), QPointF(235, 150), QPointF(200, 150)):
        event = QMouseEvent(QMouseEvent.Type.MouseButtonPress, point, point,
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        canvas.mousePressEvent(event)
    win.dynamic_curve._dispatch()
    wait_until(win.dynamic_curve.can_apply)
    assert len(seen) == 1 and seen[0]["goals"] == {"accuracy_pct": 90, "hit_click_ms": 500}
    source = seen[0]["sources_kept_separate"][0]
    assert source["source"] == "training" and source["window_attempts"] == 3
    assert source["window_accuracy_pct"] == pytest.approx(100 / 3)
    assert len(source["recent_attempts"]) == 3 and source["recent_attempts"][-1]["click_ms"] >= 500
    assert "moving_target" in source and "scenario" in source
    assert win.dynamic_curve.proposed.horizontal[1].multiplier == pytest.approx(.96)
    assert win.calibration_page._profile is None
    canvas.stop()
    win.dynamic_curve.set_enabled(False)
    win.close()


def test_enabled_live_monitor_preserves_graph_and_raw_arena_geometry(qapp, monkeypatch):
    from fps_coach.ui import main_window, raw_mouse_capture

    class FakeCapture:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def stop(self):
            return None

    monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture", FakeCapture)
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    win = main_window.MainWindow()
    _KEEP.append(win)
    win.resize(1350, 900)
    win.show()
    win._select_page("calibration")
    win.dynamic_curve.set_enabled(True)
    QTest.qWait(80)
    page = win.calibration_page
    chart = page.chart
    before = (chart.size(), chart.mapTo(page, chart.rect().topLeft()))
    page.task_settings_btn.click()
    QTest.qWait(80)
    assert (chart.size(), chart.mapTo(page, chart.rect().topLeft())) == before
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("raw"))
    QTest.qWait(80)  # allow the input-path description to settle before capture
    page.recalibrate_btn.click()
    QTest.qWait(80)
    arena = page.canvas.geometry()
    assert page.canvas._active
    assert not win.recommendation_page.dynamic_curve_panel.enabled_box.isEnabled()
    assert not page.chart._editable
    page.canvas._raw_waiting()
    win.dynamic_curve.status = "Local AI changed its status while raw capture is active."
    win.dynamic_curve.changed.emit()
    QTest.qWait(100)
    assert page.canvas.geometry() == arena and not page.canvas._paused_for_navigation
    page.reset_btn.click()
    assert page.chart._editable
    win.dynamic_curve.set_enabled(False)
    win.close()
