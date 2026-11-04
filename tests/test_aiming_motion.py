"""Self-reported motion informs the coach, never the measured curve."""

from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.aiming_motion import OPTIONS, motion_context
from fps_coach.backend import _FallbackAnalyzer


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP = []


def _trial():
    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0, 0, 100, 0, 0)
    for i, x in enumerate((20, 60, 125, 110), 1):
        analyzer.add_sample(x, 0, i * 0.01)
    assert analyzer.end_trial(100, 0, 0.05)
    return analyzer.summary(), analyzer.recommend()


def test_each_choice_has_distinct_non_diagnostic_guidance_and_safe_context():
    assert [o.key for o in OPTIONS] == ["wrist", "fingers", "arm", "shoulder_hybrid"]
    assert len({o.flick for o in OPTIONS}) == len(OPTIONS)
    assert len({o.tracking for o in OPTIONS}) == len(OPTIONS)
    assert len({o.fatigue for o in OPTIONS}) == len(OPTIONS)
    for option in OPTIONS:
        context = motion_context(option.key)
        assert option.label in context
        assert "not a measured joint contribution" in context
        assert "not adjusted from this label" in context
        assert "do not infer pain" in context
    assert motion_context("unknown") == ""


def test_exclusive_selection_show_guidance_and_clear(qapp):
    from fps_coach.ui.calibration_wizard import CalibrationWizard

    dialog = CalibrationWizard()
    _KEEP.append(dialog)
    dialog.go_next()
    dialog.go_next()
    for option in OPTIONS:
        card = dialog.cards[2][option.key]
        card.click()
        assert dialog.answers["motion"] == option.key
        assert sum(button.isChecked() for button in dialog.cards[2].values()) == 1
        for key in ("flick", "tracking", "fatigue"):
            assert getattr(option, key) in card.accessibleName()
    dialog.cards[2]["unsure"].click()
    assert dialog.answers["motion"] == "unsure"
    assert not any(dialog.cards[2][o.key].isChecked() for o in OPTIONS)
    dialog.skip_btn.click()
    assert dialog.answers["motion"] == ""
    dialog.close()


def test_keyboard_selects_option(qapp):
    from fps_coach.ui.calibration_wizard import CalibrationWizard
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog = CalibrationWizard()
    _KEEP.append(dialog)
    dialog.show()
    dialog.go_next()
    dialog.go_next()
    qapp.processEvents()
    dialog.cards[2]["fingers"].setFocus()
    QTest.keyClick(dialog.cards[2]["fingers"], Qt.Key.Key_Space)
    assert dialog.answers["motion"] == "fingers"
    dialog.close()


def test_coach_context_tracks_selection_and_scores_without_modifying_curve(qapp):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    _KEEP.append(page)
    summary, recommendation = _trial()
    page.set_calibration_context(summary, recommendation, provisional=True)
    measured = deepcopy(page._calibrated_profile)
    rec = deepcopy(page._last_recommendation)
    for option in OPTIONS:
        page.set_aiming_motion(option.key)
        assert option.label in page._calibration_context
        assert "User-reported primary flick motion" not in page._vision_context
        assert f"Overshoot score: {summary['overshoot_score']:.6f}" in page._calibration_context
        assert "provisional" in page._calibration_context
        assert page._calibrated_profile == measured
        assert page._last_recommendation == rec
    page.begin_calibration_session()
    assert "Shoulder / Hybrid" in page._calibration_context
    assert "no accepted targets yet" in page._calibration_context
    page.set_aiming_motion("")
    assert "User-reported primary" not in page._calibration_context
    page.close()


def test_motion_change_invalidates_old_ai_result_and_preview(qapp):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    _KEEP.append(page)
    summary, rec = _trial()
    page.set_calibration_context(summary, rec)
    page.set_aiming_motion("wrist")
    page._apply_ai_adjustment("[ADJUST: sensitivity +5%]")
    assert page._adjusted_profile is not None
    revision = page._calibration_revision
    page.set_aiming_motion("arm")
    assert page._adjusted_profile is None
    page._on_reply("Stale response [ADJUST: sensitivity +5%]", revision)
    assert page._adjusted_profile is None
    assert "Stale response" not in page.transcript.toPlainText()
    assert "ask again" in page.transcript.toPlainText()
    page.close()


def test_question_is_drafted_only_and_preserves_user_edits(qapp):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    _KEEP.append(page)
    page.set_aiming_motion("wrist")
    assert page.prepare_motion_question()
    assert "Wrist" in page.input.text()
    assert page._thread is None and page._messages == []
    page.set_aiming_motion("arm")
    assert "Arm" in page.input.text() and "Wrist" not in page.input.text()
    page.input.setText("My own draft")
    page.set_aiming_motion("fingers")
    assert page.input.text() == "My own draft"
    assert not page.prepare_motion_question()
    assert page.input.text() == "My own draft"
    assert "existing draft was kept" in page.transcript.toPlainText()
    page.close()


def test_unedited_motion_draft_is_removed_on_clear(qapp):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    _KEEP.append(page)
    page.set_aiming_motion("fingers")
    page.prepare_motion_question()
    page.set_aiming_motion("")
    assert page.input.text() == ""
    page.close()


def test_selection_is_session_only_and_clear_history_removes_it(qapp):
    from fps_coach.ui.pages_setup import DashboardPage

    first = DashboardPage()
    second = DashboardPage()
    _KEEP.extend([first, second])
    first._open_calibration_wizard()
    dialog = first._wizard_dialog
    dialog.go_next()
    dialog.go_next()
    dialog.cards[2]["arm"].click()
    for _ in range(5):
        dialog.go_next()
    assert first._wizard_answers["motion"] == "arm"
    assert second._wizard_answers["motion"] == ""
    first._clear_wizard()
    assert first._wizard_answers["motion"] == ""
    first.close()
    second.close()


def test_main_window_wires_selection_to_coach_and_optional_draft(qapp):
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    _KEEP.append(window)
    window.show()
    dashboard = window.dashboard_page
    dashboard.wizard_btn._animations_enabled = lambda: False
    dashboard.wizard_btn.click()
    dialog = dashboard._wizard_dialog
    dialog.go_next()
    dialog.go_next()
    dialog.cards[2]["shoulder_hybrid"].click()
    for _ in range(5):
        dialog.go_next()
    assert window.chat_page._aiming_motion == "shoulder_hybrid"
    assert "Shoulder / Hybrid" in window.chat_page._calibration_context
    dashboard._open_calibration_wizard()
    dashboard._wizard_dialog.saved_discuss_btn.click()
    assert window.stack.currentWidget() is window.chat_page
    assert "guide answers" in window.chat_page.input.text()
    assert window.chat_page._thread is None
    window._on_calibration_cleared()
    assert not dashboard._wizard_answers["motion"] and not window.chat_page._aiming_motion
    assert "User-reported primary" not in window.chat_page._calibration_context
    window.close()


def test_provider_payload_contains_current_motion_and_measured_summary(qapp, monkeypatch):
    from fps_coach.ai_chat import AIChatClient, ChatSettings
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    _KEEP.append(page)
    summary, rec = _trial()
    page.set_calibration_context(summary, rec, provisional=True)
    page.set_aiming_motion("fingers")
    assert page.prepare_motion_question()
    client = AIChatClient(
        ChatSettings(provider="ollama", base_url="http://example.invalid", model="test")
    )
    calls = []
    monkeypatch.setattr(client, "ensure_ollama_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        client,
        "_post",
        lambda url, payload, headers: (
            calls.append(payload) or {"message": {"content": "Test response"}}
        ),
    )
    client.chat(
        [{"role": "user", "content": page.input.text()}], curve_context=page._calibration_context
    )
    systems = [m["content"] for m in calls[0]["messages"] if m["role"] == "system"]
    context = next(c for c in systems if "User-reported primary flick motion" in c)
    assert "Fingers" in context and "provisional" in context
    assert f"Overshoot score: {summary['overshoot_score']:.6f}" in context
    assert "not a measured joint contribution" in context
    assert "raw cursor positions" not in context
    page.close()


def test_guide_motion_does_not_touch_calibration_analyzer_or_curve(qapp):
    from fps_coach.ui.pages_setup import DashboardPage

    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0, 0, 100, 0, 0)
    for i, x in enumerate((20, 60, 125, 110), 1):
        analyzer.add_sample(x, 0, i * 0.01)
    assert analyzer.end_trial(100, 0, 0.05)
    before_summary, before_curve = deepcopy(analyzer.summary()), deepcopy(analyzer.recommend())
    page = DashboardPage()
    _KEEP.append(page)
    for option in OPTIONS:
        page._open_calibration_wizard()
        dialog = page._wizard_dialog
        dialog.go_next()
        dialog.go_next()
        dialog.cards[2][option.key].click()
        for _ in range(3):
            dialog.go_next()
        assert analyzer.summary() == before_summary
        assert analyzer.recommend() == before_curve
    page.close()
