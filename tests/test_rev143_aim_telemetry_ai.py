"""In-app aggregate telemetry and preview-only local AI session proposals."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from fps_coach import aim_telemetry_ai, scenarios
from fps_coach.aim_telemetry_ai import analyze_aim_telemetry, recommend_sensitivity_profile
from fps_coach.analytics import AnalyticsStore, SessionResult
from fps_coach.curve_io import CurvePoint, CurveProfile
from fps_coach.guide_ai import GuideAIError
from fps_coach.performance_telemetry import PerformanceTelemetry
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def base_profile():
    points = [CurvePoint(0, 1), CurvePoint(100, 1)]
    return CurveProfile("base", deepcopy(points), deepcopy(points))


def completed(scenario="flick", *, summary=None):
    return SessionResult("2026-10-30T10:00:00", scenario, 3, 1, .75, 500, 420, summary)


def wait(predicate):
    end = time.monotonic() + 4
    while not predicate() and time.monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def test_real_click_and_event_sampled_tracking_variances():
    t = PerformanceTelemetry("training")
    t.begin_target(1, 0, 0)
    t.move(9, 0, 1.2)
    t.observe_tracking(9, 0, 10, 0, 2, 1.2)
    t.move(14, 0, 1.3)
    t.observe_tracking(14, 0, 10, 0, 2, 1.3)
    t.click(11, 0, 10, 0, 2, 1.5)
    t.click(14, 0, 10, 0, 2, 1.7)
    summary = t.session_summary()
    summary.update(target_radius_px=2, moving_target=True, target_speed_px_s=100)
    analysis = analyze_aim_telemetry(summary, {"accuracy_pct": 80, "click_ms": 400})
    assert analysis["accuracy_pct"] == 50
    assert analysis["accuracy_delta_percentage_points"] == -30
    assert analysis["hit_click_time_delta_ms"] == pytest.approx(100)
    assert analysis["mean_endpoint_error_px"] == pytest.approx(2.5)
    assert analysis["mean_endpoint_error_radii"] == pytest.approx(1.25)
    assert analysis["tracking_event_count"] == 2
    assert analysis["tracking_on_target_pct"] == 50
    assert analysis["tracking_mean_error_radii"] == pytest.approx(1.25)
    assert "not time-on-target" in analysis["limitations"]
    assert "recent_attempts" not in summary and "coordinates" not in repr(summary)
    empty = analyze_aim_telemetry(PerformanceTelemetry("training").session_summary())
    assert empty["accuracy_pct"] is None and empty["tracking_on_target_pct"] is None
    with pytest.raises(ValueError):
        analyze_aim_telemetry({"attempts": 1, "hits": 2})


def test_profile_recommendation_uses_separate_sessions_and_no_heuristic_fallback(monkeypatch):
    seen = {}
    def model(provider, url, model_name, goals, metrics, profile, **kwargs):
        seen.update(kwargs["session_context"])
        seen["metrics"] = metrics
        return {"anchors": [(0, 1), (.5, .98), (1, 1.03)], "reason": "A small experiment."}
    monkeypatch.setattr(aim_telemetry_ai, "analyze_performance", model)
    summary = {"attempts": 4, "hits": 3, "mean_hit_click_ms": 500,
               "mean_endpoint_error_radii": .8, "tracking_event_count": 10,
               "tracking_on_target_pct": 70, "tracking_mean_error_radii": .6,
               "target_radius_px": 20, "moving_target": True, "target_speed_px_s": 190}
    sessions = [completed("flick"), completed("tracking", summary=summary)]
    result = recommend_sensitivity_profile("ollama", "http://localhost:11434", "test", sessions,
                                           "tracking", base_profile(), {"accuracy_pct": 90, "click_ms": 600})
    assert result["session_count"] == 2 and seen["playstyle"] == "tracking"
    assert seen["sessions"][0]["scenario"] == "flick" and "observed_aim" not in seen["sessions"][0]
    assert seen["sessions"][1]["observed_aim"]["tracking_event_count"] == 10
    assert seen["sessions"][1]["observed_aim"]["accuracy_delta_percentage_points"] == -15
    assert seen["metrics"]["training"]["window_attempts"] == 8
    with pytest.raises(GuideAIError):
        recommend_sensitivity_profile("ollama", "http://localhost:11434", "test", [], "tracking", base_profile(), {})
    with pytest.raises(GuideAIError):
        recommend_sensitivity_profile("ollama", "http://localhost:11434", "test", sessions, "invented", base_profile(), {})
    def fail(*args, **kwargs):
        raise GuideAIError("model offline")
    monkeypatch.setattr(aim_telemetry_ai, "analyze_performance", fail)
    with pytest.raises(GuideAIError, match="offline"):
        recommend_sensitivity_profile("ollama", "http://localhost:11434", "test", sessions,
                                      "balanced", base_profile(), {"accuracy_pct": 90, "click_ms": 600})


def test_real_local_validation_and_prompt_are_bounded(monkeypatch):
    from fps_coach import dynamic_curve_ai
    calls = []
    def request(opener, url, payload, timeout):
        calls.append((url, payload))
        if url.endswith("/tags"):
            return {"models": [{"name": "test"}]}
        if url.endswith("/show"):
            return {"model_info": {"local": True}}
        return {"message": {"content": json.dumps({"gain_factors": [
            {"speed_fraction": 0, "factor": 1}, {"speed_fraction": .5, "factor": .97},
            {"speed_fraction": 1, "factor": 1.03}], "reason": "Compare carefully."})}}
    monkeypatch.setattr(dynamic_curve_ai, "_json_request", request)
    kwargs = dict(sessions=[completed()], playstyle="flick", profile=base_profile(),
                  goals={"accuracy_pct": 90, "click_ms": 600})
    result = recommend_sensitivity_profile("ollama", "http://localhost:11434", "test", **kwargs)
    assert len(result["anchors"]) == 3
    data = json.loads(calls[-1][1]["messages"][1]["content"])
    assert data["session_analysis"]["playstyle"] == "flick"
    assert "recent_attempts" not in repr(data) or data["sources_kept_separate"][0]["recent_attempts"] == []
    calls.clear()
    with pytest.raises(GuideAIError):
        recommend_sensitivity_profile("ollama", "http://example.com", "test", **kwargs)
    assert calls == []


def test_legacy_history_load_and_opt_in_completed_summary(app, tmp_path):
    from fps_coach.ui.pages_practice import TrainingPage
    path = tmp_path / "analytics.json"
    path.write_text(json.dumps([{"timestamp": "2026-01-01", "scenario": "flick", "hits": 3, "misses": 1,
                                "accuracy": .75, "avg_reaction_ms": 500, "best_reaction_ms": 400}]))
    store = AnalyticsStore.load(path)
    assert len(store.sessions) == 1 and store.sessions[0].aim_telemetry is None
    page = TrainingPage(store)
    _KEEP.append(page)
    page.resize(900, 900)
    page.show()
    canvas = page.canvas
    canvas.resize(600, 450)
    app.processEvents()
    scenario = replace(scenarios.get("tracking"), target_count=2)
    canvas.set_aim_monitor_enabled(True)
    canvas.start(scenario)
    for _ in range(2):
        point = canvas._target.toPoint()
        QTest.mouseMove(canvas, point + QPoint(2, 0))
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()
    assert not canvas._active and len(store.sessions) == 2
    saved = store.sessions[-1].aim_telemetry
    assert saved and saved["attempts"] == 2 and saved["moving_target"]
    assert saved["tracking_event_count"] >= 1
    on_disk = json.loads(path.read_text())[-1]["aim_telemetry"]
    assert "recent_attempts" not in on_disk and "x" not in on_disk and "y" not in on_disk
    assert AnalyticsStore.load(path).sessions[-1].aim_telemetry == on_disk
    canvas.start(scenario)
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas._target.toPoint())
    canvas.stop()
    assert store.sessions[-1].aim_telemetry is None  # incomplete drills are not saved as aim summaries
    canvas.set_aim_monitor_enabled(False)
    canvas.start(scenario)
    for _ in range(2):
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas._target.toPoint())
    assert store.sessions[-1].aim_telemetry is None
    page.close()


def test_coach_preview_confirmation_and_stale_rejection(app, monkeypatch, tmp_path):
    from fps_coach.ui import pages_coach
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_coach, "_settings", lambda: settings)
    monkeypatch.setattr(pages_coach, "recommend_sensitivity_profile", lambda *a, **k: {
        "anchors": [(0, 1), (.5, .98), (1, 1.03)], "reason": "Small test.", "session_count": 1})
    page = pages_coach.AIChatPage()
    _KEEP.append(page)
    store = AnalyticsStore(sessions=[completed()])
    baseline = base_profile()
    goals = {"accuracy_pct": 90, "click_ms": 600}
    page.set_aim_analysis_sources(store, lambda: baseline, lambda: goals)
    page.show()
    events = []
    page.curve_adjusted.connect(lambda *args: events.append(args))
    assert not page.aim_monitor.isChecked()
    page.aim_playstyle.setCurrentIndex(page.aim_playstyle.findData("tracking"))
    page._request_aim_analysis()
    wait(lambda: not page._aim_running)
    assert page._aim_preview_job is not None and not page.apply_to_rec.isHidden()
    assert baseline.horizontal[-1].multiplier == 1 and events == []
    page._send_adjusted_to_calibration()
    assert len(events) == 1 and events[0][1].horizontal[-1].multiplier == pytest.approx(1.03)
    page._request_aim_analysis()
    wait(lambda: not page._aim_running)
    goals["click_ms"] = 700
    page._send_adjusted_to_calibration()
    assert len(events) == 1 and "fresh" in page.aim_status.text().lower()
    page.aim_monitor.setChecked(True)
    page.clear_aim_history()
    assert not page.aim_monitor.isChecked() and page._aim_preview_job is None
    page.close()


def test_change_during_model_request_discards_stale_reply(app, monkeypatch, tmp_path):
    import threading

    from fps_coach.ui import pages_coach
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_coach, "_settings", lambda: settings)
    started, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        started.set()
        release.wait(3)
        return {"anchors": [(0, 1), (.5, .98), (1, 1.03)], "reason": "test", "session_count": 1}
    monkeypatch.setattr(pages_coach, "recommend_sensitivity_profile", slow)
    page = pages_coach.AIChatPage()
    _KEEP.append(page)
    page.set_aim_analysis_sources(AnalyticsStore(sessions=[completed()]), base_profile,
                                  lambda: {"accuracy_pct": 90, "click_ms": 600})
    page._request_aim_analysis()
    assert started.wait(1)
    page.aim_playstyle.setCurrentIndex(page.aim_playstyle.findData("precision"))
    release.set()
    wait(lambda: not page._aim_running)
    assert page._adjusted_profile is None and page.apply_to_rec.isHidden()
    assert "fresh" in page.aim_status.text().lower()
    page.close()


def test_main_window_opt_in_wiring_and_clear(app, monkeypatch, tmp_path):
    from fps_coach.ui import main_window, pages_coach
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_window, "_settings", lambda: settings)
    monkeypatch.setattr(pages_coach, "_settings", lambda: settings)
    monkeypatch.setattr(main_window.AnalyticsStore, "load", lambda _path: AnalyticsStore())
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    win = main_window.MainWindow()
    _KEEP.append(win)
    try:
        chat, canvas = win.chat_page, win.training_page.canvas
        assert not chat.aim_monitor.isChecked() and not canvas._aim_monitor_enabled
        chat.aim_monitor.setChecked(True)
        assert canvas._aim_monitor_enabled and not win.dynamic_curve.enabled
        canvas.aim_monitor_updated.emit({"attempts": 2, "hits": 1,
                                         "mean_hit_click_ms": 700, "tracking_event_count": 4,
                                         "tracking_on_target_pct": 75, "moving_target": True})
        assert "-40.0 pp" in chat.aim_status.text() and "not time-on-target" in chat.aim_status.text()
        win.privacy_page.training_cleared.emit()
        assert not chat.aim_monitor.isChecked() and not canvas._aim_monitor_enabled
    finally:
        win.close()


def test_enable_mid_drill_does_not_retroactively_record_input(app, tmp_path):
    from fps_coach.ui.pages_practice import TrainingPage
    store = AnalyticsStore(path=tmp_path / "history.json")
    page = TrainingPage(store)
    _KEEP.append(page)
    page.resize(900, 900)
    page.show()
    canvas = page.canvas
    canvas.resize(600, 450)
    app.processEvents()
    scenario = replace(scenarios.get("flick"), target_count=2)
    canvas.start(scenario)
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas._target.toPoint())
    canvas.set_aim_monitor_enabled(True)
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas._target.toPoint())
    summary = store.sessions[-1].aim_telemetry
    assert summary["attempts"] == 1 and summary["hits"] == 1
    assert summary["observation_partial"] is True
    assert store.sessions[-1].hits == 2  # basic session analytics remain complete
    canvas.set_aim_monitor_enabled(False)
    page.close()
