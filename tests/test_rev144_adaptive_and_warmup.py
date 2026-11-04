"""Opt-in adaptive calibration and miss-based local warm-up."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fps_coach.adaptive_difficulty import AdaptiveDifficulty
from fps_coach.analytics import AnalyticsStore, SessionResult
from fps_coach.backend import _FallbackAnalyzer
from fps_coach.guide_ai import GuideAIError
from fps_coach.ui import pages_setup, raw_mouse_capture
from fps_coach.ui.pages_setup import CalibrationPage
from fps_coach.warmup_ai import generate_personalized_warmup
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_raw_calibration import FakeAPI
from test_rev140_raw_targets_and_point_settings import _move_and_click

_KEEP = []


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(predicate):
    end = time.monotonic() + 4
    while not predicate() and time.monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def sample_result(scenario="flick", hits=4, misses=2):
    return SessionResult("2026-10-30", scenario, hits, misses, 2 / 3, 500, 300)


def test_adaptive_controller_needs_evidence_and_scales_with_bounds():
    controller = AdaptiveDifficulty()
    assert controller.radius == 18 and controller.speed == 40
    controller.record(True)
    controller.record(True)
    assert controller.radius == 18 and controller.speed == 40
    controller.record(True)
    assert controller.radius < 18 and controller.speed > 40
    for _ in range(120):
        controller.record(True)
    assert controller.level == 6 and 10 <= controller.radius <= 26 and controller.speed <= 120
    for _ in range(120):
        controller.record(False)
    assert controller.level == -4 and controller.radius <= 26 and controller.speed >= 10
    controller.reset()
    assert controller.accuracy is None and controller.radius == 18


def test_moving_target_metrics_reference_current_center():
    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0, 0, 100, 0, 1)
    analyzer.add_sample(60, 0, 1.1)
    analyzer.add_sample(130, 0, 1.2)
    analyzer.set_target(130, 0)
    assert analyzer.end_trial(130, 0, 1.3)
    assert analyzer.summary()["mean_endpoint_error_px"] == pytest.approx(0)


def test_adaptive_raw_run_completes_and_locks_settings(app, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_setup, "_settings", lambda: settings)
    api = FakeAPI()
    capture = raw_mouse_capture.RawMouseCapture
    monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture", lambda arena, move, click, lost, **kw:
                        capture(arena, move, click, lost, api=api, **kw))
    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP.append(page)
    page.resize(1280, 900)
    page.show()
    page.activateWindow()
    QTest.qWait(30)
    page.canvas.set_input_mode("raw")
    assert not page.adaptive_targets.isChecked()
    page.adaptive_targets.setChecked(True)
    assert page.canvas.adaptive_enabled
    completions, telemetry = [], []
    page.canvas.set_performance_enabled(True)
    page.canvas.performance_updated.connect(telemetry.append)
    page.canvas.completed.connect(lambda summary, rec: completions.append((summary, rec)))
    page.recalibrate_btn.click()
    assert not page.adaptive_targets.isEnabled() and page.canvas._target_timer.isActive()
    start_target = page.canvas._target
    QTest.qWait(50)
    assert page.canvas._target != start_target
    for n in range(12):
        QTest.qWait(18)
        assert page.canvas._raw_capture is not None, (n, page.hint.text(), page.canvas._paused_for_navigation)
        _move_and_click(page.canvas, api)
        assert page.canvas._completed_trials == n + 1
        if n == 2:
            page.canvas._paused_for_navigation = True
            target = page.canvas._target
            QTest.qWait(40)
            assert page.canvas._target == target
            page.canvas._paused_for_navigation = False
    assert telemetry[0]["recent_attempts"][-1]["target_radius_px"] == 18
    assert telemetry[-1]["recent_attempts"][-1]["target_speed_px_s"] > 40
    summary, _ = completions[0]
    assert summary["trial_count"] == 12 and summary["adaptive_calibration"] is True
    assert summary["mean_endpoint_error_px"] < 2  # uses moving target's actual click-time center
    assert summary["adaptive_target_speed_px_s"] > 40
    assert summary["adaptive_target_radius_px"] < 18
    assert not page.canvas._target_timer.isActive() and page.adaptive_targets.isEnabled()
    page.close()


def test_incompatible_analyzer_cannot_enable_moving_targets(app):
    class Old:
        pass
    page = CalibrationPage(SimpleNamespace(analyzer=Old()))
    _KEEP.append(page)
    assert not page.adaptive_targets.isEnabled()
    with pytest.raises(RuntimeError):
        page.canvas.set_adaptive_enabled(True)
    page.close()


def test_warmup_local_request_validates_evidence_and_model(monkeypatch):
    from fps_coach import warmup_ai
    calls = []
    def request(opener, url, payload, timeout):
        calls.append((url, payload))
        if url.endswith("/tags"):
            return {"models": [{"name": "test"}]}
        if url.endswith("/show"):
            return {"model_info": {"ok": True}}
        value = {"guidance": "Warm up gently.", "routine": [
            {"scenario": "flick", "minutes": 3, "reason": "Recent flick misses."},
            {"scenario": "tracking", "minutes": 4, "reason": "Practice moving targets."}]}
        return {"message": {"content": json.dumps(value)}}
    monkeypatch.setattr(warmup_ai, "_json_request", request)
    history = [sample_result("flick", 4, 2), sample_result("tracking", 3, 1)]
    result = generate_personalized_warmup("ollama", "http://localhost:11434", "test", history)
    assert result["total_minutes"] == 7 and len(result["routine"]) == 2
    payload = json.loads(calls[-1][1]["messages"][1]["content"])
    assert payload["recent_training_summaries"][0]["misses"] == 2
    assert "aim_telemetry" not in repr(payload) and "timestamp" not in repr(payload)
    calls.clear()
    with pytest.raises(GuideAIError, match="No recorded misses"):
        generate_personalized_warmup("ollama", "http://localhost:11434", "test", [sample_result(misses=0)])
    assert calls == []
    with pytest.raises(GuideAIError):
        generate_personalized_warmup("ollama", "http://not-local", "test", history)
    assert calls == []
    bad = {"guidance": "test", "routine": [{"scenario": "flick", "minutes": 99, "reason": "test"}]}
    with pytest.raises(GuideAIError):
        warmup_ai._parse_warmup(json.dumps(bad))


def test_warmup_ui_shows_result_without_starting_drill(app, monkeypatch, tmp_path):
    from fps_coach.ui import pages_coach
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_coach, "_settings", lambda: settings)
    monkeypatch.setattr(pages_coach, "generate_personalized_warmup", lambda *a, **k: {
        "guidance": "Warm up gently.", "total_minutes": 5,
        "routine": [{"scenario": "flick", "minutes": 2, "reason": "Misses observed."},
                    {"scenario": "tracking", "minutes": 3, "reason": "Moving target practice."}]})
    page = pages_coach.AIChatPage()
    _KEEP.append(page)
    page.set_aim_analysis_sources(AnalyticsStore(sessions=[sample_result()]), lambda: None, lambda: {})
    page._request_warmup()
    wait(lambda: not page._warmup_running)
    assert "conversation" in page.warmup_status.text().lower()
    assert "flick practice" in page._transcript_items[-1][1].lower()
    assert "do not start automatically" in page._transcript_items[-1][1].lower()
    page.close()


def test_pending_warmup_is_discarded_after_history_clear(app, monkeypatch, tmp_path):
    import threading

    from fps_coach.ui import pages_coach
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_coach, "_settings", lambda: settings)
    started, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        started.set()
        release.wait(3)
        return {"guidance": "g", "total_minutes": 2, "routine": [
            {"scenario": "flick", "minutes": 1, "reason": "r"},
            {"scenario": "tracking", "minutes": 1, "reason": "r"}]}
    monkeypatch.setattr(pages_coach, "generate_personalized_warmup", slow)
    store = AnalyticsStore(sessions=[sample_result()])
    page = pages_coach.AIChatPage()
    _KEEP.append(page)
    page.set_aim_analysis_sources(store, lambda: None, lambda: {})
    page._request_warmup()
    assert started.wait(1)
    store.clear()
    page.clear_aim_history()
    release.set()
    wait(lambda: not page._warmup_running)
    assert not page._transcript_items and "fresh" in page.warmup_status.text().lower()
    page.close()


def test_warmup_rejects_remote_model_before_sending_session_counts(monkeypatch):
    from fps_coach import warmup_ai
    calls = []
    def request(opener, url, payload, timeout):
        calls.append(url)
        return {"models": [{"name": "test", "remote_host": "service"}]}
    monkeypatch.setattr(warmup_ai, "_json_request", request)
    with pytest.raises(GuideAIError, match="remote"):
        generate_personalized_warmup("ollama", "http://localhost:11434", "test", [sample_result()])
    assert len(calls) == 1 and calls[0].endswith("/tags")


def test_desktop_target_moves_and_stays_paused_off_page(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QCursor

    settings = QSettings(str(tmp_path / "desktop.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_setup, "_settings", lambda: settings)
    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP.append(page)
    page.resize(1280, 900)
    page.show()
    page.activateWindow()
    QTest.qWait(30)
    QCursor.setPos(page.canvas.mapToGlobal(QPoint(100, 100)))
    page.adaptive_targets.setChecked(True)
    page.canvas.start_session()
    page.canvas._outside_arena = False
    first = page.canvas._target
    QTest.qWait(60)
    assert page.canvas._target != first
    page.canvas.pause_for_navigation()
    paused = page.canvas._target
    QTest.qWait(60)
    assert page.canvas._target == paused
    page.canvas.resume_after_navigation()
    page.canvas._outside_arena = False
    resumed = page.canvas._target
    QTest.qWait(60)
    assert page.canvas._target != resumed
    page.canvas.cancel_session()
    assert not page.canvas._target_timer.isActive()
    page.close()
