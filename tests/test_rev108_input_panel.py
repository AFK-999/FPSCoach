"""Calibration settings use the shared icon and an inline, session-safe panel."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fps_coach.backend import _FallbackAnalyzer
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from fps_coach.ui import pages_setup

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_setup, "_settings", lambda: store)
    return store


_KEEP = []


def _page():
    from fps_coach.ui.pages_setup import CalibrationPage

    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP.append(page)
    page.resize(1400, 950)
    page.show()
    QTest.qWait(40)
    return page


def test_standard_settings_icon_and_inline_keyboard_toggle(qapp, settings):
    from fps_coach.analytics import AnalyticsStore
    from fps_coach.ui.pages_coach import AIChatPage
    from fps_coach.ui.pages_practice import TrainingPage

    page = _page()
    coach = AIChatPage()
    training = TrainingPage(AnalyticsStore())
    _KEEP.extend((coach, training))
    button = page.task_settings_btn
    for shared in (coach.voice_settings_toggle, training.settings_toggle):
        assert (button.property("glyph"), button.property("glyph_size"), button.objectName()) == (
            shared.property("glyph"), shared.property("glyph_size"), shared.objectName()
        )
    assert button.accessibleName() == "Calibration input settings"
    assert button.isCheckable() and not button.isChecked()
    assert page.input_settings_panel.isHidden()
    assert not page.findChildren(QDialog)
    assert page.input_settings_panel.window() is page.window()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert button.isChecked() and page.input_settings_panel.isVisible()
    assert "Hide" in button.toolTip()
    assert page.input_mode_combo.window() is page.window()
    assert page.sample_rate_combo.window() is page.window()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert not button.isChecked() and page.input_settings_panel.isHidden()
    assert "Show" in button.toolTip()
    page.close()
    coach.close()
    training.close()


def test_existing_two_paths_rate_and_warning_survive_toggle(qapp, settings):
    page = _page()
    page.task_settings_btn.click()
    assert [page.input_mode_combo.itemData(i) for i in range(page.input_mode_combo.count())] == ["raw", "desktop"]
    assert "not detected or compensated" in page.telemetry_note.text()
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("raw"))
    assert page.canvas.input_mode == "raw" and not page.sample_rate_combo.isEnabled()
    assert "virtual logical pixel" in page.input_note.text()
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("desktop"))
    page.sample_rate_combo.setCurrentIndex(page.sample_rate_combo.findData(500))
    assert page.canvas.input_mode == "desktop" and page.canvas.sample_rate_hz == 500
    assert settings.value("calibration/sample_hz", type=int) == 500
    page.task_settings_btn.click()
    page.task_settings_btn.click()
    assert page.sample_rate_combo.currentData() == 500
    assert page.canvas.sample_rate_hz == 500
    page.close()


@pytest.mark.parametrize("panel_open", [False, True])
@pytest.mark.parametrize("mode", ["raw", "desktop"])
def test_active_session_locks_toggle_and_prevents_panel_reflow(qapp, settings, monkeypatch, panel_open, mode):
    from fps_coach.ui import raw_mouse_capture

    class FakeCapture:
        def __init__(self, *args, **kwargs):
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True
            return None

    monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture", FakeCapture)
    page = _page()
    page.task_settings_btn.click()
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData(mode))
    if not panel_open:
        page.task_settings_btn.click()
    QTest.qWait(100)
    before = page.canvas.geometry()
    page.recalibrate_btn.click()
    QTest.qWait(100)
    assert page.canvas._active
    assert not page.task_settings_btn.isEnabled()
    assert not page.input_mode_combo.isEnabled() and not page.sample_rate_combo.isEnabled()
    assert page.input_settings_panel.isHidden() is (not panel_open)
    assert page.canvas.geometry() == before
    assert not page.canvas._paused_for_navigation
    page.task_settings_btn.click()
    assert page.input_settings_panel.isHidden() is (not panel_open)
    if mode == "raw":
        assert page.canvas._raw_capture is not None
    page.reset_btn.click()
    QTest.qWait(50)
    assert not page.canvas._active and page.task_settings_btn.isEnabled()
    assert page.input_mode_combo.isEnabled()
    assert page.sample_rate_combo.isEnabled() is (mode == "desktop")
    page.task_settings_btn.click()
    assert page.input_settings_panel.isHidden() is panel_open
    page.close()


def test_failed_raw_start_keeps_panel_available(qapp, settings, monkeypatch):
    from fps_coach.ui import raw_mouse_capture

    class Unavailable:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("raw input unavailable")

    monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture", Unavailable)
    page = _page()
    page.task_settings_btn.click()
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("raw"))
    page.recalibrate_btn.click()
    assert not page.canvas._active
    assert page.task_settings_btn.isEnabled() and page.input_mode_combo.isEnabled()
    assert "could not start" in page.hint.text().lower()
    assert "raw input unavailable" in page.hint.text().lower()
    page.close()


def test_smaller_window_keeps_inline_controls_reachable(qapp, settings):
    page = _page()
    page.resize(900, 700)
    page.task_settings_btn.click()
    QTest.qWait(75)
    panel = page.input_settings_panel
    assert panel.isVisible()
    for item in (page.input_mode_combo, page.input_note, page.telemetry_note,
                 page.sample_rate_combo, page.input_settings_session_note):
        assert item.isVisible()
        point = item.mapTo(panel, item.rect().bottomRight())
        assert panel.rect().contains(point)
    page.close()


def test_successful_completion_unlocks_the_inline_settings(qapp, settings):
    page = _page()
    page.task_settings_btn.click()
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("desktop"))
    page.recalibrate_btn.click()
    assert page.canvas._active and not page.task_settings_btn.isEnabled()
    # The canvas sets active false before emitting completed; exercise the same
    # signal wiring without fabricating mouse input or hardware measurements.
    page.canvas._active = False
    page.canvas._sample_timer.stop()
    page.canvas.completed.emit(
        {"trial_count": 1},
        {"points": [(0.0, 0.0), (0.5, 0.4), (1.0, 1.0)], "captured_sample_count": 4},
    )
    assert page.task_settings_btn.isEnabled()
    assert page.input_mode_combo.isEnabled() and page.sample_rate_combo.isEnabled()
    assert page.input_settings_panel.isVisible()
    page.task_settings_btn.click()
    assert page.input_settings_panel.isHidden()
    page.close()


@pytest.mark.parametrize("size", [(900, 700), (1200, 800), (1600, 1000)])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_expanding_settings_does_not_resize_or_move_curve(qapp, settings, size, theme):
    from fps_coach.ui.styles import build_style

    original = qapp.styleSheet()
    page = None
    try:
        qapp.setStyleSheet(build_style("balanced", theme))
        page = _page()
        page.resize(*size)
        QTest.qWait(80)
        chart = page.chart
        before = (chart.size(), chart.mapTo(page, chart.rect().topLeft()))
        page.task_settings_btn.click()
        QTest.qWait(100)
        assert page.input_settings_panel.isVisible()
        assert (chart.size(), chart.mapTo(page, chart.rect().topLeft())) == before
        page.task_settings_btn.click()
        QTest.qWait(100)
        assert (chart.size(), chart.mapTo(page, chart.rect().topLeft())) == before
    finally:
        if page is not None:
            page.close()
        qapp.setStyleSheet(original)


def test_curve_resizes_with_window_not_with_settings(qapp, settings):
    page = _page()
    page.resize(1200, 750)
    QTest.qWait(60)
    small = page.chart.height()
    page.resize(1200, 1050)
    QTest.qWait(100)
    large = page.chart.height()
    assert large > small
    page.task_settings_btn.click()
    QTest.qWait(100)
    assert page.chart.height() == large
    page.close()


def test_raw_hint_changes_do_not_resize_or_interrupt_the_arena(qapp, settings, monkeypatch):
    from fps_coach.ui import raw_mouse_capture
    from fps_coach.ui.styles import build_style

    class FakeCapture:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            return None

    monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture", FakeCapture)
    original = qapp.styleSheet()
    page = None
    try:
        qapp.setStyleSheet(build_style("balanced", "dark"))
        page = _page()
        page.resize(1200, 800)
        QTest.qWait(80)
        page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("raw"))
        old_minimum, old_maximum = page.canvas.minimumSize(), page.canvas.maximumSize()
        page.recalibrate_btn.click()
        QTest.qWait(80)
        before = page.canvas.geometry()
        page.canvas._raw_waiting()
        QTest.qWait(100)
        assert page.canvas.geometry() == before
        assert not page.canvas._paused_for_navigation
        assert "no raw movement" in page.hint.text().lower()
        page.canvas._raw_move(3, 2)
        QTest.qWait(100)
        assert page.canvas.geometry() == before
        assert not page.canvas._paused_for_navigation
        assert "movement detected" in page.hint.text().lower()
        page.reset_btn.click()
        assert page.canvas.minimumSize() == old_minimum
        assert page.canvas.maximumSize() == old_maximum
    finally:
        if page is not None:
            page.close()
        qapp.setStyleSheet(original)
