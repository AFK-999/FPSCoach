"""CC4-inspired graph behavior, explicitly not exact driver interpolation."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.curve_io import CurvePoint, CurveProfile, load_curve, save_curve
from fps_coach.curve_spline import weighted_spline
from fps_coach.ui.widgets import CurveChart
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

pytest_plugins = ["test_rev132_numerical_curve_axes"]


def weighted_profile():
    points = [CurvePoint(0, .6, .2), CurvePoint(10, 1.4, .9),
              CurvePoint(25, .8, .3), CurvePoint(50, 1.8, .7)]
    return CurveProfile("Weights", points, deepcopy(points), source_format="cc4")


def test_spline_is_weight_sensitive_bounded_and_does_not_claim_exact_interpolation():
    points = [(0, .2), (.33, .9), (.66, .2), (1, .2)]
    low = weighted_spline(points, [.5, .05, .5, .5])
    high = weighted_spline(points, [.5, 1, .5, .5])
    assert low[0] == high[0] == points[0]
    assert low[-1] == high[-1] == points[-1]
    assert high[len(high) // 2][1] > low[len(low) // 2][1]
    assert all(0 <= x <= 1 and .2 <= y <= .9 for x, y in high)
    assert [x for x, _ in high] == sorted(x for x, _ in high)
    assert weighted_spline([(0, .5), (1, .5)]) == [(0, .5), (1, .5)]


def test_cc4_weights_roundtrip_without_affecting_cc3_preview(make_window, tmp_path):
    win = make_window()
    page = win.calibration_page
    p = weighted_profile()
    page._profile = deepcopy(p)
    page._set_profile_chart(p)
    before = page.chart.grab().toImage()
    page.chart.set_curve_weights([1.0, .01, .99, .25])
    after = page.chart.grab().toImage()
    assert before == after  # CC4 weights do not change the CC3-style preview
    path = save_curve(page._profile, tmp_path / "test.cc4")
    saved = load_curve(path)
    assert [v.smoothness for v in saved.horizontal] == pytest.approx([.2, .9, .3, .7])
    win.close()


def test_all_sensitivity_views_receive_actual_profile_weights(make_window):
    win = make_window()
    old = weighted_profile()
    new = deepcopy(old)
    new.horizontal[1].smoothness = .1
    win.calibration_page.set_adjusted(old, new, "preview")
    win.recommendation_page.show_curve_profile(new)
    win.chat_page._show_preview(old, new, "preview")
    win.dynamic_curve.base, win.dynamic_curve.proposed = old, new
    win.dynamic_curve.changed.emit()
    charts = [win.calibration_page.chart, win.recommendation_page.chart,
              win.chat_page.quick_preview, win.recommendation_page.dynamic_curve_panel.chart,
              win.training_page.dynamic_curve_panel.chart, win.training_page.dynamic_curve_monitor.chart]
    for chart in charts:
        assert chart.weights == [.2, .1, .3, .7]
        assert "approx" in chart.accessibleDescription().lower()
    for chart in (charts[0], charts[2], *charts[3:]):
        assert chart.previous_weights == [.2, .9, .3, .7]
    win.close()


def test_simultaneous_xy_drag_commits_exact_control_point_and_preserves_others(make_window):
    win = make_window()
    page = win.calibration_page
    p = weighted_profile()
    page._profile = deepcopy(p)
    page._set_profile_chart(p)
    chart = page.chart
    ys, xs = list(chart.points), chart.x_positions()
    ys[1] = .65
    xs[1] = .3
    page._on_curve_adjusted(ys, xs)
    assert page._profile.horizontal[1].speed == pytest.approx(15)
    assert page._profile.horizontal[1].multiplier == pytest.approx(.65 * chart.axis_y_max)
    assert page._profile.horizontal[0] == p.horizontal[0]
    assert page._profile.horizontal[2:] == p.horizontal[2:]
    page.undo()
    assert page._profile == p
    win.close()


def test_zoom_pan_reset_and_point_mapping_on_readonly_chart(qapp):
    chart = CurveChart(editable=False)
    chart.resize(500, 260)
    chart.points, chart.xs = [.2, .8, .5], [0, .5, 1]
    chart.set_axis_scale(50, 2, "profile units")
    chart.show()
    qapp.processEvents()
    rect = chart._plot_rect()
    center = rect.center()
    wheel = QWheelEvent(QPointF(center), QPointF(chart.mapToGlobal(center)), QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(chart, wheel)
    assert chart._view[1] - chart._view[0] == pytest.approx(.9)
    x, y = chart.value_at(center)
    mapped = chart._pt(rect, x, y).toPoint()
    assert abs(mapped.x() - center.x()) <= 1 and abs(mapped.y() - center.y()) <= 1
    before = chart._view
    QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(chart, center + QPoint(30, 10))
    QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=center + QPoint(30, 10))
    assert chart._view != before
    QTest.mouseClick(chart, Qt.MouseButton.MiddleButton, pos=center)
    assert chart._view == (0, 1, 0, 1)
    chart.close()


def test_add_delete_weight_history_roundtrips(make_window):
    win = make_window()
    page = win.calibration_page
    original = weighted_profile()
    page._profile = deepcopy(original)
    page._set_profile_chart(original)
    chart = page.chart
    spot = chart._pt(chart._plot_rect(), .3, .5).toPoint()
    index = chart.add_point(spot)
    assert index == 2
    assert chart.weights == pytest.approx([.2, .9, .5, .3, .7])
    assert [p.smoothness for p in page._profile.horizontal] == pytest.approx(chart.weights)
    page.undo()
    assert chart.weights == pytest.approx([.2, .9, .3, .7])
    assert page._profile == original
    page.redo()
    assert chart.weights == pytest.approx([.2, .9, .5, .3, .7])
    assert chart.remove_point(2)
    assert chart.weights == pytest.approx([.2, .9, .3, .7])
    page.undo()
    assert chart.weights == pytest.approx([.2, .9, .5, .3, .7])
    win.close()


def test_drag_moves_one_control_point_without_touching_neighbors(qapp):
    chart = CurveChart()
    chart.resize(500, 260)
    chart.points = [.2, .5, .8, .7]
    chart.xs = [0, .33, .66, 1]
    chart.show()
    qapp.processEvents()
    chart._drag_base = list(chart.points)
    chart._drag_x_base = chart.x_positions()
    chart._move_handle(1, .65)
    assert chart.points == pytest.approx([.2, .65, .8, .7])
    chart.close()


def test_export_warns_about_unmeasured_scale_and_large_cc4(make_window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    win = make_window()
    page = win.calibration_page
    points = [CurvePoint(i, 1, .5) for i in range(65)]
    page._profile = CurveProfile("Large", points, deepcopy(points))
    path = tmp_path / "large.cc4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    page._save()
    assert len(load_curve(path).horizontal) == 65  # never silently drop data
    assert "normalized" in page.hint.text() and "64 points" in page.hint.text()
    win.close()


def test_edit_does_not_clamp_an_untouched_zero_control_point(make_window):
    win = make_window()
    page = win.calibration_page
    p = weighted_profile()
    p.horizontal[0].multiplier = 0
    p.vertical[0].multiplier = 0
    page._profile = deepcopy(p)
    page._set_profile_chart(p)
    ys, xs = list(page.chart.points), page.chart.x_positions()
    ys[1] = .5
    page._on_curve_adjusted(ys, xs)
    assert page._profile.horizontal[0].multiplier == 0
    assert page._profile.vertical[0].multiplier == 0
    win.close()
