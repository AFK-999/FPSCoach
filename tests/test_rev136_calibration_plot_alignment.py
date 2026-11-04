"""Calibration plot aligns every stored point with its interpolation path."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.curve_io import (
    CurvePoint,
    CurveProfile,
    load_curve,
    recommendation_profile,
    save_curve,
)
from fps_coach.curve_spline import interpolating_segments
from fps_coach.ui.widgets import CurveChart
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

pytest_plugins = ["test_rev132_numerical_curve_axes"]


def _path_anchor_points(path):
    anchors = []
    i = 0
    while i < path.elementCount():
        e = path.elementAt(i)
        if e.type == QPainterPath.ElementType.CurveToElement:
            end = path.elementAt(i + 2)
            anchors.append(QPointF(end.x, end.y))
            i += 3
        else:
            anchors.append(QPointF(e.x, e.y))
            i += 1
    return anchors


def _assert_all_anchors(chart, points, xs):
    rect = chart._plot_rect()
    path = chart._curve_path(rect, points, xs)
    anchors = _path_anchor_points(path)
    for x, y in zip(xs, points, strict=True):
        target = chart._pt(rect, x, y)
        assert any(abs(target.x() - a.x()) < 1e-9 and abs(target.y() - a.y()) < 1e-9 for a in anchors), target


def test_grid_extents_and_coordinate_inverse_are_consistent(qapp):
    chart = CurveChart(interpolate_points=True)
    chart.resize(503, 263)
    rect = chart._plot_rect()
    assert chart._pt(rect, 0, 1) == QPointF(rect.topLeft())
    assert chart._pt(rect, 1, 0) == QPointF(rect.bottomRight())
    assert chart.value_at(rect.topLeft()) == pytest.approx((0, 1))
    assert chart.value_at(rect.bottomRight()) == pytest.approx((1, 0))
    chart._view = (.2, 1.2, .1, 1.5)
    for x, y in [(.2, .1), (1.2, 1.5), (.41, .75), (.8, .4)]:
        screen = chart._pt(rect, x, y)
        spot = chart.value_at(screen.toPoint())
        assert spot is not None
        pixel = chart._pt(rect, *spot)
        assert abs(screen.x() - pixel.x()) <= .51
        assert abs(screen.y() - pixel.y()) <= .51
    chart.close()


def test_interpolating_path_contains_every_anchor_for_nonuniform_and_duplicate_x(qapp):
    chart = CurveChart(interpolate_points=True)
    chart.resize(623, 350)
    xs = [0, .003, .17, .17, .43, .91, 1]
    ys = [.03, .8, .2, .9, .35, .7, .99]
    chart.points, chart.xs = ys, xs
    assert chart.dot_indices() == list(range(len(ys)))
    for view in [(0, 1, 0, 1), (.1, .8, .2, .9), (0, 2.5, 0, 1.6)]:
        chart._view = view
        _assert_all_anchors(chart, ys, xs)
    segments = interpolating_segments(list(zip(xs, ys, strict=True)))
    assert segments[2] == ((.17, .9),)  # repeated x remains a vertical segment
    chart.close()


def test_cubic_interpolation_does_not_overshoot_each_segment():
    points = [(0, .1), (.00003, .8), (.2, .85), (.6, .25), (.9, .25), (1, .95)]
    for start, segment in zip(points, interpolating_segments(points), strict=False):
        if len(segment) == 1:
            continue
        c1, c2, end = segment
        low, high = sorted((start[1], end[1]))
        for i in range(101):
            t = i / 100
            y = ((1 - t)**3 * start[1] + 3 * (1 - t)**2 * t * c1[1]
                 + 3 * (1 - t) * t**2 * c2[1] + t**3 * end[1])
            assert low - 1e-12 <= y <= high + 1e-12


def test_calibration_profile_overlay_edit_and_export_keep_coordinates(make_window, tmp_path):
    win = make_window()
    page = win.calibration_page
    old_points = [CurvePoint(0, .2, .2), CurvePoint(1.23456789, 1.9, .8, True),
                  CurvePoint(80, .7, .1), CurvePoint(125, 2.2, .6)]
    old = CurveProfile("Old", old_points, deepcopy(old_points), source_format="ccurve", ccurve_tail=["17"])
    current = deepcopy(old)
    current.horizontal[2].multiplier = 4
    current.vertical[2].multiplier = 4
    page.set_adjusted(old, current, "overlay")
    chart = page.chart
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    _assert_all_anchors(chart, chart.previous_points, chart.previous_xs)
    for index, point in enumerate(current.horizontal):
        assert chart.x_positions()[index] * chart.axis_x_max == pytest.approx(point.speed, abs=1e-13)
        assert chart.points[index] * chart.axis_y_max == pytest.approx(point.multiplier, abs=1e-13)
    page.point_combo.setCurrentIndex(3)
    page.point_velocity.setText("93.12345678912345")
    page.point_sensitivity.setText("5.12345678912345")
    page.point_apply_btn.click()
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    _assert_all_anchors(chart, chart.previous_points, chart.previous_xs)
    p = page._profile
    assert p.horizontal[2].speed == float("93.12345678912345")
    path = save_curve(p, tmp_path / "aligned.ccurve")
    reloaded = load_curve(path)
    assert [(v.speed, v.multiplier, v.corner) for v in reloaded.horizontal] == [
        (v.speed, v.multiplier, v.corner) for v in p.horizontal
    ]
    assert reloaded.ccurve_tail == ["17"]
    assert p.horizontal[1].smoothness == .8  # CC4 weights remain in memory; CC3 cannot store them
    cc4 = load_curve(save_curve(p, tmp_path / "aligned.cc4"))
    assert cc4.horizontal[1].smoothness == .8
    page.undo()
    assert page._profile == current
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    page.redo()
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    assert not win.recommendation_page.chart._interpolate_points  # other CC3-style previews unchanged
    win.close()


def test_large_calibration_profile_all_points_are_drawn_and_on_path(make_window):
    win = make_window()
    page = win.calibration_page
    values = [CurvePoint(i / 10, .6 + ((i * 83) % 53) / 100) for i in range(180)]
    p = CurveProfile("long", values, deepcopy(values))
    page._profile = deepcopy(p)
    page._set_profile_chart(p)
    chart = page.chart
    assert chart.dot_indices() == list(range(180))
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    chart._view = (.4, .7, .4, .8)
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    win.close()


def test_equal_points_and_tiny_speed_differences_are_preserved(qapp):
    chart = CurveChart(interpolate_points=True)
    chart.resize(500, 270)
    xs = [0.0, 1e-301, 1e-300, 1e-12, 1.0]
    ys = [.2, .3, .7, .1, .8]
    _assert_all_anchors(chart, ys, xs)
    chart.close()


def test_zero_speed_and_tiny_gains_share_the_exact_overlay_scale(make_window):
    win = make_window()
    a_pts = [CurvePoint(0, 1e-14), CurvePoint(0, 1e-14)]
    b_pts = [CurvePoint(0, 1.5e-14), CurvePoint(.2, 2e-14)]
    a = CurveProfile("zero", a_pts, deepcopy(a_pts), source_format="ccurve")
    b = CurveProfile("tiny", b_pts, deepcopy(b_pts), source_format="ccurve")
    win.calibration_page.set_adjusted(a, b, "tiny")
    win.chat_page._show_preview(a, b, "tiny")
    win.dynamic_curve.base, win.dynamic_curve.proposed = a, b
    win.dynamic_curve.changed.emit()
    charts = [win.calibration_page.chart, win.chat_page.quick_preview,
              win.recommendation_page.dynamic_curve_panel.chart,
              win.training_page.dynamic_curve_monitor.chart]
    for chart in charts:
        assert chart.axis_x_max == .2 and chart.axis_y_max == 2e-14
        for coordinate, point in zip(chart.x_positions(), b.horizontal, strict=True):
            assert coordinate * chart.axis_x_max == pytest.approx(point.speed, abs=0, rel=1e-14)
        for coordinate, point in zip(chart.points, b.horizontal, strict=True):
            assert coordinate * chart.axis_y_max == pytest.approx(point.multiplier, abs=0, rel=1e-14)
        for coordinate, point in zip(chart.previous_points, a.horizontal, strict=True):
            assert coordinate * chart.axis_y_max == pytest.approx(point.multiplier, abs=0, rel=1e-14)
    _assert_all_anchors(win.calibration_page.chart, win.calibration_page.chart.points,
                        win.calibration_page.chart.x_positions())
    win.close()


def test_live_recommendation_maps_profile_points_not_raw_output_speed(make_window):
    win = make_window()
    page = win.calibration_page
    page.canvas._active = True
    recommendation = {"points": [(0, 0), (.2, .3), (.5, .65), (1, 1.15)],
                      "sensitivity_multiplier": 1.1, "captured_sample_count": 48}
    expected = recommendation_profile(recommendation)
    page._on_live_recommendation({"trial_count": 1}, recommendation)
    chart = page.chart
    for x, y, point in zip(chart.x_positions(), chart.points, expected.horizontal, strict=True):
        assert x * chart.axis_x_max == pytest.approx(point.speed)
        assert y * chart.axis_y_max == pytest.approx(point.multiplier)
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    assert "model control points" in page.curve_status.text()
    page.canvas._active = False
    win.close()


def test_mouse_drag_uses_same_geometry_and_line_follows_point(make_window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = make_window()
    page = win.calibration_page
    points = [CurvePoint(0, .2), CurvePoint(30, 1.3), CurvePoint(90, .8), CurvePoint(100, 2)]
    p = CurveProfile("mouse", points, deepcopy(points))
    page._profile = deepcopy(p)
    page._set_profile_chart(p)
    chart = page.chart
    rect = chart._plot_rect()
    origin = chart._pt(rect, .3, .65).toPoint()
    target = chart._pt(rect, .55, .4).toPoint()
    expected = chart.value_at(target)
    QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(chart, target)
    QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=target)
    assert page._profile.horizontal[1].speed == pytest.approx(expected[0] * chart.axis_x_max)
    assert page._profile.horizontal[1].multiplier == pytest.approx(expected[1] * chart.axis_y_max)
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    page.undo()
    assert page._profile == p
    _assert_all_anchors(chart, chart.points, chart.x_positions())
    win.close()
