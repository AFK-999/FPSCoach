"""AI Coach Focus: Capture, Calibration and Training toggles, and the master switch.

Capture gives the coach its settings only. No test here, and no code path, reads the screen
or gameplay. The AI request itself is replaced with a recorder, so no model is called.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

KEYS = ("coach/focus/capture", "coach/focus/calibration", "coach/focus/training", "capture/preset", "capture/fps")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(qapp):
    from fps_coach.ui.widgets import _settings

    s = _settings()
    before = {key: s.value(key) for key in KEYS}
    for key in KEYS[:3]:
        s.remove(key)  # every test starts from the default: all off
    yield s
    for key, value in before.items():
        if value is None:
            s.remove(key)
        else:
            s.setValue(key, value)
    s.sync()


@pytest.fixture()
def page(qapp, store):
    from fps_coach.ui.pages_coach import AIChatPage

    p = AIChatPage()
    calls: list[str] = []
    p._maybe_proactive_ai = lambda instruction: calls.append(instruction)  # no AI request
    p.ai_calls = calls
    yield p
    p.close()


def _auto_notes(page) -> list[str]:
    return [t for t in page.transcript.toPlainText().split("\n") if "Coach (auto)" in t]


def _calibration():
    summary = {"path_efficiency": 0.8, "overshoot_score": 0.1, "correction_score": 0.2}
    rec = {"title": "Slightly lower", "sensitivity_multiplier": 0.95, "confidence": 0.6, "reasons": []}
    return summary, rec


def _session(scenario: str = "flick"):
    from fps_coach.analytics import SessionResult

    return SessionResult.create(scenario, [300.0, 320.0, 340.0], 8, 2)


# -- the toggles and the master switch -------------------------------------------

def test_everything_starts_off(page) -> None:
    assert all(not box.isChecked() for box in page.focus_boxes.values())
    assert page.focus_master.isChecked() is False


def test_master_turns_all_three_on_and_off(page, store) -> None:
    page.focus_master.setChecked(True)
    assert all(box.isChecked() for box in page.focus_boxes.values())
    for key in ("capture", "calibration", "training"):
        assert store.value(f"coach/focus/{key}", type=bool) is True

    page.focus_master.setChecked(False)
    assert all(not box.isChecked() for box in page.focus_boxes.values())
    assert store.value("coach/focus/training", type=bool) is False


def test_one_toggle_leaves_the_others_alone(page, store) -> None:
    page.focus_boxes["training"].setChecked(True)
    assert page.focus_boxes["training"].isChecked()
    assert not page.focus_boxes["capture"].isChecked()
    assert not page.focus_boxes["calibration"].isChecked()
    assert store.value("coach/focus/training", type=bool) is True
    assert store.value("coach/focus/capture", type=bool) is False


def test_master_follows_the_individual_toggles(page) -> None:
    page.focus_boxes["capture"].setChecked(True)
    page.focus_boxes["calibration"].setChecked(True)
    assert page.focus_master.isChecked() is False
    page.focus_boxes["training"].setChecked(True)
    assert page.focus_master.isChecked() is True
    page.focus_boxes["calibration"].setChecked(False)
    assert page.focus_master.isChecked() is False


def test_saved_focus_is_restored_when_the_page_opens(qapp, store) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    store.setValue("coach/focus/calibration", True)
    p = AIChatPage()
    try:
        assert p.focus_boxes["calibration"].isChecked()
        assert not p.focus_boxes["training"].isChecked()
    finally:
        p.close()


# -- Calibration -------------------------------------------------------------------

def test_calibration_notes_only_when_calibration_focus_is_on(page) -> None:
    summary, rec = _calibration()
    page.notify_new_calibration(summary, rec)
    assert _auto_notes(page) == []
    assert page.ai_calls == []

    page.focus_boxes["calibration"].setChecked(True)
    page.notify_new_calibration(summary, rec)
    assert len(_auto_notes(page)) == 1
    assert len(page.ai_calls) == 1
    assert "completed a new calibration" in page.ai_calls[0]


# -- Training ----------------------------------------------------------------------

def test_training_notes_only_when_training_focus_is_on(page) -> None:
    page.notify_new_training(_session())
    assert _auto_notes(page) == []
    assert page.ai_calls == []

    page.focus_boxes["training"].setChecked(True)
    page.notify_new_training(_session())
    notes = _auto_notes(page)
    assert len(notes) == 1
    from fps_coach.ui.pages_coach import _scenario_label

    assert _scenario_label("flick") in notes[0]
    assert "Accuracy 80%" in notes[0]
    assert len(page.ai_calls) == 1
    assert "finished a training drill" in page.ai_calls[0]


# -- Capture: settings only --------------------------------------------------------

def test_capture_focus_adds_settings_to_the_context_and_nothing_else(page, store) -> None:
    store.setValue("capture/preset", "performance")
    store.setValue("capture/fps", 30)
    assert "FPS" not in page._calibration_context

    page.focus_boxes["capture"].setChecked(True)
    assert "preset 'performance', 30 FPS" in page._calibration_context
    assert "cannot see the screen or gameplay" in page._calibration_context

    page.focus_boxes["capture"].setChecked(False)
    assert "FPS" not in page._calibration_context


def test_focus_note_says_capture_cannot_see_the_screen(page) -> None:
    page.focus_boxes["capture"].setChecked(True)
    assert "cannot see the screen or gameplay" in page.focus_note.text()


# -- automatic answers never change the curve -------------------------------------

def test_automatic_answer_is_shown_but_not_applied(page, monkeypatch) -> None:
    applied: list[str] = []
    monkeypatch.setattr(page, "_apply_ai_adjustment", lambda answer: applied.append(answer))
    page._proactive_received("Try a multiplier of 0.9.")
    assert applied == []
    assert "Try a multiplier of 0.9." in page.transcript.toPlainText()


# -- main window: a finished drill reaches the coach -------------------------------

def test_finished_drill_reaches_the_coach_through_the_main_window(qapp, store, monkeypatch) -> None:
    from fps_coach.ui.main_window import MainWindow

    store.setValue("coach/focus/training", True)
    window = MainWindow()
    try:
        monkeypatch.setattr(window.chat_page, "_maybe_proactive_ai", lambda instruction: None)
        window.training_page.session_recorded.emit(_session())
        assert any("Training drill finished" in t for t in _auto_notes(window.chat_page))
    finally:
        window.close()
