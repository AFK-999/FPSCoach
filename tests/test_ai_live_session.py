"""Coach calibration context is current-session evidence, never a preset template."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.ai_chat import curve_context
from fps_coach.backend import _FallbackAnalyzer


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP_ALIVE = []


def _page(qapp):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    _KEEP_ALIVE.append(page)
    return page


def _trial(analyzer, kind, time_offset=0.0):
    analyzer.begin_trial(0, 0, 100, 0, time_offset)
    xs = [20, 65, 125, 110] if kind == "overshoot" else [15, 40, 65, 80]
    for i, x in enumerate(xs, 1):
        analyzer.add_sample(x, 0, time_offset + i * .01)
    assert analyzer.end_trial(100 if kind == "overshoot" else 80, 0, time_offset + .05)
    return analyzer.summary(), analyzer.recommend()


def test_context_contains_current_scores_and_model_points_not_raw_positions():
    analyzer = _FallbackAnalyzer()
    summary, rec = _trial(analyzer, "overshoot")
    context = curve_context(summary, rec, provisional=True)
    assert "in progress; provisional" in context
    assert "Trials: 1" in context
    assert "Captured movement samples in window: 5" in context
    assert f"Overshoot score: {summary['overshoot_score']:.6f}" in context
    assert "Model control points (normalized input, output; not raw samples)" in context
    for x, y in rec["points"]:
        assert f"({x:.6f},{y:.6f})" in context
    assert "raw cursor positions" not in context
    assert "No accepted" in curve_context({}, {})


def test_each_accepted_target_updates_the_coach_from_its_current_session(qapp):
    page = _page(qapp)
    analyzer = _FallbackAnalyzer()
    page.begin_calibration_session()
    assert page._calibrated_profile is None
    summary1, rec1 = _trial(analyzer, "overshoot")
    page.set_calibration_context(summary1, rec1, provisional=True)
    first_context = page._calibration_context
    first_profile = deepcopy(page._calibrated_profile)
    summary2, rec2 = _trial(analyzer, "underreach", 1.0)
    page.set_calibration_context(summary2, rec2, provisional=True)
    assert page._calibration_context != first_context
    assert page._last_recommendation["points"] == rec2["points"]
    assert page._calibrated_profile != first_profile
    assert "Trials: 2" in page._calibration_context
    assert "Provisional calibration curve" in page.quick_hint.text()
    assert len(page.quick_preview.points) == len(rec2["points"])


def test_new_session_and_cancel_cannot_reuse_old_calibration(qapp):
    page = _page(qapp)
    analyzer = _FallbackAnalyzer()
    summary, rec = _trial(analyzer, "overshoot")
    page.set_calibration_context(summary, rec)
    old_revision = page._calibration_revision
    page.begin_calibration_session()
    assert page._calibrated_profile is None and page._last_recommendation is None
    assert "no accepted targets yet" in page._calibration_context
    assert page._calibration_revision > old_revision
    page._apply_ai_adjustment("[ADJUST: sensitivity +5%]")
    assert "Complete a current-session" in page.quick_hint.text()
    analyzer.reset()
    summary2, rec2 = _trial(analyzer, "underreach")
    page.set_calibration_context(summary2, rec2, provisional=True)
    assert rec2["points"] != rec["points"]
    assert f"Underreach score: {summary2['underreach_score']:.6f}" in page._calibration_context
    page.clear_calibration_context()
    assert page._calibrated_profile is None and page._last_recommendation is None
    assert "No accepted" in page._calibration_context


def test_stale_ai_response_cannot_override_newer_calibration(qapp):
    page = _page(qapp)
    analyzer = _FallbackAnalyzer()
    summary, rec = _trial(analyzer, "overshoot")
    page.set_calibration_context(summary, rec, provisional=True)
    old = page._calibration_revision
    summary2, rec2 = _trial(analyzer, "underreach", 1.0)
    page.set_calibration_context(summary2, rec2, provisional=True)
    profile = deepcopy(page._calibrated_profile)
    page._on_reply("OLD DATA [ADJUST: sensitivity +5%]", old)
    page._proactive_received("STALE PROPOSAL [ADJUST: precision]", old)
    assert "OLD DATA" not in page.transcript.toPlainText()
    assert "STALE PROPOSAL" not in page.transcript.toPlainText()
    assert page._calibrated_profile == profile
    assert page._adjusted_profile is None
    assert "ask again" in page.transcript.toPlainText()


def test_ai_numeric_preview_uses_current_profile_but_preset_directives_do_not(qapp):
    page = _page(qapp)
    analyzer = _FallbackAnalyzer()
    summary, rec = _trial(analyzer, "overshoot")
    page.set_calibration_context(summary, rec)
    measured = deepcopy(page._calibrated_profile)
    page._apply_ai_adjustment("Try a little more. [ADJUST: sensitivity +5%]")
    assert page._preview_base_profile == measured
    assert len(page._adjusted_profile.horizontal) == len(rec["points"])
    for before, after in zip(measured.horizontal, page._adjusted_profile.horizontal, strict=True):
        assert after.multiplier == pytest.approx(before.multiplier * 1.05)
    page._apply_ai_adjustment("Try a precision template. [ADJUST: precision]")
    assert page._adjusted_profile is None
    assert "no preset curve was generated" in page.quick_hint.text()
    assert page.apply_to_rec.isHidden()
    page._apply_ai_adjustment("Overshoot is 8%. Reduce sensitivity.")
    assert page._adjusted_profile is None  # score percentages are not change directives


def test_provisional_ai_preview_cannot_be_sent_to_calibration(qapp):
    page = _page(qapp)
    analyzer = _FallbackAnalyzer()
    summary, rec = _trial(analyzer, "overshoot")
    page.set_calibration_context(summary, rec, provisional=True)
    events = []
    page.curve_adjusted.connect(lambda *args: events.append(args))
    page._apply_ai_adjustment("[ADJUST: sensitivity +5%]")
    assert not page.apply_to_rec.isEnabled()
    page._send_adjusted_to_calibration()
    assert events == []
    page.set_calibration_context(summary, rec, provisional=False)
    page._apply_ai_adjustment("[ADJUST: sensitivity +5%]")
    page._send_adjusted_to_calibration()
    assert len(events) == 1


def test_main_window_wires_live_updates_and_pauses_capture_away_from_calibration(qapp, monkeypatch):
    from fps_coach.ui.main_window import MainWindow
    from PySide6.QtCore import QPointF

    window = MainWindow()
    _KEEP_ALIVE.append(window)
    window.show()
    window._select_page("calibration")
    canvas = window.calibration_page.canvas
    canvas.start_session(3)
    assert window.chat_page._calibration_provisional
    assert not window.recommendation_page.generate_btn.isEnabled()
    analyzer = window._backend.analyzer
    # Supply one actual accepted target, then send exactly the canvas's live signal.
    summary, rec = _trial(analyzer, "overshoot")
    canvas.recommendation_updated.emit(summary, rec)
    assert window.chat_page._last_recommendation["points"] == rec["points"]
    assert "Trials: 1" in window.chat_page._calibration_context
    before = len(analyzer._trial["samples"]) if analyzer._trial else 0
    window._select_page("chat")
    assert canvas._paused_for_navigation and not canvas._sample_timer.isActive()
    canvas._sample_cursor()
    assert (len(analyzer._trial["samples"]) if analyzer._trial else 0) == before
    window._select_page("calibration")
    assert not canvas._paused_for_navigation and canvas._sample_timer.isActive()
    assert int(analyzer.summary()["trial_count"]) == 1
    monkeypatch.setattr(canvas, "_cursor_global", lambda: QPointF(100000, 100000))
    monkeypatch.setattr(canvas, "isVisible", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    before = len(analyzer._trial["samples"])
    canvas._sample_cursor()
    assert len(analyzer._trial["samples"]) == before
    assert canvas._outside_arena
    canvas.cancel_session()
    assert window.chat_page._calibrated_profile is None
    assert window.recommendation_page.generate_btn.isEnabled()
    window.close()


def test_queued_worker_reply_uses_request_revision_on_gui_thread(qapp):
    from fps_coach.ui.pages_coach import AIChatPage
    from PySide6.QtCore import QThread, Signal

    class Request(QThread):
        response_ready = Signal(str)

        def run(self):
            self.response_ready.emit("[ADJUST: sensitivity +5%] old response")

    page = AIChatPage()
    _KEEP_ALIVE.append(page)
    summary, rec = _trial(_FallbackAnalyzer(), "overshoot")
    page.set_calibration_context(summary, rec, provisional=True)
    worker = Request()
    worker._calibration_revision = page._calibration_revision
    worker.response_ready.connect(page._on_reply)
    worker.start()
    assert worker.wait(5000)
    # The queued reply cannot be delivered until the GUI event loop runs.
    page.set_calibration_context(summary, rec, provisional=True)
    qapp.processEvents()
    assert page._adjusted_profile is None
    assert "old response" not in page.transcript.toPlainText()
    page.close()
