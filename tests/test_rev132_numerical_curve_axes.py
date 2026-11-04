"""Numeric velocity/gain axes must reproduce stored profile control points."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.curve_io import (
    CurvePoint,
    CurveProfile,
    chart_axis_scale,
    chart_points,
    recommendation_profile,
)
from PySide6.QtCore import QEvent, QSettings
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import dynamic_curve, main_window, pages_setup

    def create(theme="dark"):
        settings = QSettings(str(tmp_path / f"{theme}.ini"), QSettings.Format.IniFormat)
        settings.setValue("ui/theme", theme)
        settings.setValue("ui/animations", False)
        for module in (dynamic_curve, main_window, pages_setup):
            monkeypatch.setattr(module, "_settings", lambda: settings)
        monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
        win = main_window.MainWindow()
        _KEEP.append(win)
        win.resize(1400, 900)
        win.show()
        QTest.qWait(20)
        return win
    return create


def profile(source="generated"):
    pts = [CurvePoint(0, 0.75), CurvePoint(12.5, 1.25), CurvePoint(30.5, 2.125)]
    return CurveProfile("Test", pts, deepcopy(pts), source_format=source)


def assert_current(chart, expected):
    assert [x * chart.axis_x_max for x in chart.x_positions()] == pytest.approx(
        [p.speed for p in expected.horizontal])
    assert [y * chart.axis_y_max for y in chart.points] == pytest.approx(
        [p.multiplier for p in expected.horizontal])


def test_profile_axis_scales_use_real_values_and_honest_provenance():
    a, b = profile("cc4"), profile("cc4")
    b.horizontal[-1].multiplier = 3.5
    assert chart_axis_scale(a, b) == (30.5, 3.5, "profile units")
    assert chart_axis_scale(profile()) == (30.5, 2.125, "normalized")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_recommendation_and_calibration_show_exported_gain_not_output_speed(make_window, theme):
    win = make_window(theme)
    result = {"points": [(0, 0), (.25, .5), (.5, .6), (1, 1)],
              "sensitivity_multiplier": 1.2, "title": "Result"}
    expected = recommendation_profile(result)
    win.recommendation_page.update_result({"trial_count": 8}, result)
    assert_current(win.recommendation_page.chart, expected)
    assert win.recommendation_page.chart.axis_x_units == "normalized"
    assert win.recommendation_page.chart.axis_x_max == 100
    win.calibration_page.apply_recommendation(result)
    assert_current(win.calibration_page.chart, expected)
    assert win.calibration_page._profile == expected
    win.close()


def test_import_preview_dynamic_and_training_charts_share_true_scales(make_window):
    win = make_window()
    a, b = profile("cc4"), profile("cc4")
    b.horizontal[1].multiplier = 1.6
    b.horizontal[-1].multiplier = 3.5
    win.calibration_page.set_adjusted(a, b, "Preview")
    assert_current(win.calibration_page.chart, b)
    assert win.calibration_page.chart.axis_x_units == "profile units"
    win.chat_page._show_preview(a, b, "Preview")
    assert_current(win.chat_page.quick_preview, b)
    assert win.chat_page.quick_preview.axis_y_max == 3.5
    win.dynamic_curve.base = a
    win.dynamic_curve.proposed = b
    win.dynamic_curve.changed.emit()
    for chart in (win.recommendation_page.dynamic_curve_panel.chart,
                  win.training_page.dynamic_curve_panel.chart,
                  win.training_page.dynamic_curve_monitor.chart):
        assert_current(chart, b)
        assert chart.axis_x_units == "profile units" and chart.axis_y_max == 3.5
        assert [y * chart.axis_y_max for y in chart.previous_points] == pytest.approx(
            [p.multiplier for p in a.horizontal])
    win.close()


def test_edit_clamps_reflect_real_profile_and_undo_restores_axis_scale(make_window):
    win = make_window()
    page = win.calibration_page
    a = profile("cc4")
    page._profile = deepcopy(a)
    x_max, y_max, units = chart_axis_scale(a)
    pairs = chart_points(a)
    page._set_chart([y for _, y in pairs], xs=[x for x, _ in pairs],
                    scale=x_max, gain_scale=y_max, units=units)
    changed = list(page.chart.points)
    changed[1] = 0.0
    page._on_curve_adjusted(changed, page.chart.x_positions())
    assert page._profile.horizontal[1].multiplier == pytest.approx(.05)
    assert_current(page.chart, page._profile)
    page.undo()
    assert page._profile == a
    assert_current(page.chart, a)
    assert page.chart.axis_x_units == "profile units"
    page.redo()
    assert_current(page.chart, page._profile)
    win.close()


def test_previous_line_rescaled_for_comparison_and_cancel_restores_axes(make_window):
    win = make_window()
    page = win.calibration_page
    a, b = profile(), profile()
    b.horizontal[-1].multiplier = 4.25
    page._profile = deepcopy(a)
    page._set_profile_chart(a)
    before = page._snapshot()
    page._set_profile_chart(b)
    assert_current(page.chart, b)
    assert [y * page.chart.axis_y_max for y in page.chart.previous_points] == pytest.approx(
        [p.multiplier for p in a.horizontal])
    page._restore(before, "Restored")
    assert_current(page.chart, a)
    assert page.chart.axis_y_max == 2.125
    win.close()


def test_labels_and_hover_show_actual_values_not_unverified_counts(qapp, monkeypatch):
    from fps_coach.ui.widgets import CurveChart

    chart = CurveChart(editable=False)
    _KEEP.append(chart)
    chart.resize(500, 240)
    chart.points = [0.5, 1.0]
    chart.xs = [0.0, 1.0]
    chart.set_axis_scale(30.5, 2.125, "profile units")
    assert chart._tick_label(0.375, 0.375) == "0.375"
    assert chart._tick_label(1.125, 0.375) == "1.125"
    seen = []
    monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, _widget: seen.append(text))
    chart.show()
    qapp.processEvents()
    pos = chart._pt(chart._plot_rect(), 1.0, 1.0).toPoint()
    event = QHelpEvent(QEvent.Type.ToolTip, pos, chart.mapToGlobal(pos))
    QApplication.sendEvent(chart, event)
    assert seen and "30.5 (profile units)" in seen[0] and "2.125×" in seen[0]
    assert "counts" not in seen[0].lower()
    chart.close()


def test_recommendation_displays_adjusted_imported_profile_not_normalized_model(make_window):
    win = make_window()
    imported = profile("cc4")
    win.calibration_page._base_profile = deepcopy(imported)
    result = {"points": [(0, 0), (.5, .6), (1, 1)],
              "sensitivity_multiplier": 1.1, "title": "Result"}
    win.calibration_page.apply_recommendation(result)
    actual = deepcopy(win.calibration_page._profile)
    win._on_calibration_result({"trial_count": 8}, result)
    assert_current(win.recommendation_page.chart, actual)
    assert win.recommendation_page.chart.axis_x_units == "profile units"
    assert win.recommendation_page.chart.axis_x_max == 30.5
    win.close()
