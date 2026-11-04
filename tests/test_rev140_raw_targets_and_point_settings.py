"""Raw calibration completion and collapsible sensitivity point editor."""
from __future__ import annotations

import math
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fps_coach.backend import _FallbackAnalyzer
from fps_coach.curve_io import CurvePoint, CurveProfile
from fps_coach.ui import raw_mouse_capture as capture_mod
from fps_coach.ui.calibration import CalibrationCanvas
from fps_coach.ui.pages_setup import CalibrationPage
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_raw_calibration import FakeAPI, _packet


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def raw_page(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import pages_setup
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_setup, "_settings", lambda: settings)
    api = FakeAPI()
    capture = capture_mod.RawMouseCapture
    monkeypatch.setattr(capture_mod, "RawMouseCapture", lambda arena, move, click, lost, **kw:
                        capture(arena, move, click, lost, api=api, **kw))
    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    page.resize(1280, 900)
    page.show()
    page.activateWindow()
    QTest.qWait(50)
    page.canvas.set_input_mode("raw")
    yield page, api
    page.canvas.cancel_session()
    page.close()
    qapp.processEvents()


def _move_and_click(canvas, api, samples=4):
    """Send real raw-packet path as several integer counts and a button edge."""
    dx = round(canvas._target.x() - canvas._raw_pos.x())
    dy = round(canvas._target.y() - canvas._raw_pos.y())
    for i in range(samples):
        part_x = round(dx * (i + 1) / samples) - round(dx * i / samples)
        part_y = round(dy * (i + 1) / samples) - round(dy * i / samples)
        if part_x or part_y:
            _packet(canvas, api, dx=part_x, dy=part_y)
    _packet(canvas, api, button=1)
    if canvas._raw_capture is not None:
        _packet(canvas, api, button=2)  # RI_MOUSE_LEFT_BUTTON_UP does not double-count


def test_full_twelve_targets_through_raw_native_dispatch(raw_page, qapp):
    page, api = raw_page
    canvas = page.canvas
    progress, updates, completions = [], [], []
    canvas.progress_changed.connect(lambda n, total: progress.append((n, total)))
    canvas.recommendation_updated.connect(lambda s, r: updates.append((s, r)))
    canvas.completed.connect(lambda s, r: completions.append((s, r)))
    # Exercise the same button as the user, not just direct canvas callbacks.
    QTest.mouseClick(page.recalibrate_btn, Qt.MouseButton.LeftButton)
    assert canvas._active and canvas._raw_capture is not None
    for target in range(12):
        _move_and_click(canvas, api)
        QTest.qWait(5)
        assert canvas._completed_trials == target + 1, page.hint.text()
        if target < 11:
            assert canvas._raw_capture is not None and not canvas._paused_for_navigation, page.hint.text()
            canvas._raw_capture._on_timer()
    assert progress[-1] == (12, 12)
    assert len(updates) == 11 and len(completions) == 1
    assert completions[0][0]["trial_count"] == 12
    assert completions[0][0]["input_path"] == "raw"
    assert completions[0][0]["window_trial_count"] == 8
    assert not canvas._active and canvas._raw_capture is None and api.rect is None
    assert api.current.target == 1234


def test_single_coalesced_packet_per_target_completes_using_real_samples(raw_page):
    page, api = raw_page
    canvas = page.canvas
    page.recalibrate_btn.click()
    assert canvas._active
    for completed in range(1, 13):
        _move_and_click(canvas, api, samples=1)
        assert canvas._completed_trials == completed
    assert not canvas._active and canvas._raw_capture is None
    assert canvas._analyzer.summary()["trial_count"] == 12
    # One actual packet per target in the rolling eight-trial window; no
    # interpolated or repeated input is added to manufacture a sample count.
    result = canvas._analyzer.recommend()
    assert result["captured_sample_count"] == 8
    assert result["confidence"] <= .375
    assert any("sparse movement" in reason.lower() for reason in result["reasons"])
    assert "sparse input" in page.curve_status.text().lower()


def test_rejected_short_trial_spawns_a_recoverable_new_target(raw_page):
    page, api = raw_page
    canvas = page.canvas
    page.recalibrate_btn.click()
    assert canvas._active
    # Simulate a target too close to the current starting point: the analyzer
    # rejects direct distances below eight pixels, even on a hit.
    canvas._target = QPointF(canvas._raw_pos.x() + 2, canvas._raw_pos.y())
    canvas._begin_trial()
    _packet(canvas, api, dx=2, button=1)
    assert canvas._completed_trials == 0
    assert canvas._active and not canvas._paused_for_navigation
    assert "not enough movement data" in page.hint.text().lower()
    assert math.hypot(canvas._target.x() - canvas._raw_pos.x(),
                      canvas._target.y() - canvas._raw_pos.y()) > 8
    _move_and_click(canvas, api, samples=1)
    assert canvas._completed_trials == 1


def test_adversarial_random_target_draw_has_a_distant_fallback(qapp):
    canvas = CalibrationCanvas(_FallbackAnalyzer())
    canvas.resize(650, 430)
    canvas.set_input_mode("raw")
    canvas._raw_pos = QPointF(325, 215)
    canvas._rng.uniform = lambda a, b: (a + b) / 2
    canvas._choose_target()
    assert math.hypot(canvas._target.x() - 325, canvas._target.y() - 215) > 140
    assert 0 <= canvas._target.x() < canvas.width() and 0 <= canvas._target.y() < canvas.height()
    canvas.close()


def _profile():
    points = [CurvePoint(0, .7, .2), CurvePoint(10, 1.1, .6), CurvePoint(30, 2, .5)]
    return CurveProfile("Input", points, deepcopy(points), source_format="cc4")


def test_point_settings_start_collapsed_preserve_selection_and_edit(raw_page):
    page, _api = raw_page
    p = _profile()
    page._profile = p
    page._set_profile_chart(p)
    assert page.point_settings_panel.isHidden() and not page.point_settings_btn.isChecked()
    assert page.chart.isVisible()
    page.point_settings_btn.click()
    assert not page.point_settings_panel.isHidden()
    page.point_combo.setCurrentIndex(2)
    page.point_velocity.setText("12.75")
    page.point_sensitivity.setText("1.234")
    page.point_apply_btn.click()
    assert page._profile.horizontal[1].speed == 12.75
    assert page._profile.horizontal[1].multiplier == 1.234
    page.point_settings_btn.click()
    assert page.point_settings_panel.isHidden() and page.point_combo.currentData() == 1
    page.point_settings_btn.click()
    assert float(page.point_velocity.text()) == 12.75
    page.undo()
    assert page._profile.horizontal[1].speed == 10


def test_contextual_weight_edit_expands_and_active_run_locks_menu(raw_page):
    page, _api = raw_page
    page.graph_style_combo.setCurrentIndex(page.graph_style_combo.findData("cc4"))
    p = _profile()
    page._profile = p
    page._set_profile_chart(p)
    assert page.point_settings_panel.isHidden()
    page._on_weight_edit_requested(1)
    assert page.point_settings_btn.isChecked() and not page.point_settings_panel.isHidden()
    assert page.point_combo.currentData() == 1 and page.point_weight.isEnabled()
    page.point_settings_btn.setChecked(False)
    page.recalibrate_btn.click()
    assert not page.point_settings_btn.isEnabled()
    assert not page.point_combo.isEnabled()
    page.canvas.cancel_session()
    assert page.point_settings_btn.isEnabled()


def test_invalid_edit_opens_disclosure_and_shows_error(raw_page):
    page, _api = raw_page
    p = _profile()
    page._profile = p
    page._set_profile_chart(p)
    page.point_combo.setCurrentIndex(2)
    page.point_velocity.setText("nan")
    page.point_sensitivity.setText("1")
    page._apply_numeric_point()
    assert page.point_settings_btn.isChecked() and not page.point_error.isHidden()
    assert page._profile == p


def test_target_spawn_stays_inside_unusually_small_canvas(qapp):
    canvas = CalibrationCanvas(_FallbackAnalyzer())
    canvas.setMinimumSize(0, 0)
    canvas.resize(60, 80)
    canvas.set_input_mode("raw")
    canvas._raw_pos = QPointF(0, 0)
    for _ in range(25):
        canvas._choose_target()
        assert 0 <= canvas._target.x() < canvas.width()
        assert 0 <= canvas._target.y() < canvas.height()
    canvas.close()
