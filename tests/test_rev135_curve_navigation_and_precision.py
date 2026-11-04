"""Interactive axis navigation and precise, undoable control point edits."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.curve_io import CurvePoint, CurveProfile, load_curve, save_curve
from fps_coach.ui.widgets import CurveChart
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

pytest_plugins = ["test_rev132_numerical_curve_axes"]


def profile():
    points = [CurvePoint(0, .7), CurvePoint(10, 1.1), CurvePoint(30, 2)]
    return CurveProfile("Input", points, deepcopy(points), source_format="ccurve", ccurve_tail=["3", "7"])


def load_page(win, p):
    page = win.calibration_page
    page._profile = deepcopy(p)
    page._set_profile_chart(p)
    return page


def wheel(chart, pos, delta):
    event = QWheelEvent(QPointF(pos), QPointF(chart.mapToGlobal(pos)), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(chart, event)


def test_axis_and_plot_pan_zoom_beyond_original_data(qapp):
    chart = CurveChart(editable=False)
    chart.resize(500, 260)
    chart.points, chart.xs = [.4, .7, .8], [0, .5, 1]
    chart.set_axis_scale(100, 2, "normalized")
    chart.show()
    qapp.processEvents()
    rect = chart._plot_rect()
    wheel(chart, rect.center(), -120)
    assert chart._view[1] > 1 and chart._view[3] > 1
    # Horizontal axis dragging changes only horizontal viewport coordinates.
    pos = QPoint(rect.center().x(), rect.bottom() + 7)
    before = chart._view
    QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseMove(chart, pos + QPoint(-40, 0))
    QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=pos + QPoint(-40, 0))
    assert chart._view[0] > before[0]
    assert chart._view[2:] == before[2:]
    # Vertical axis scrolling zooms only vertically.
    rect = chart._plot_rect()
    before = chart._view
    wheel(chart, QPoint(rect.left() - 10, rect.center().y()), 120)
    assert chart._view[:2] == before[:2] and chart._view[3] - chart._view[2] < before[3] - before[2]
    # Exact mapping continues to work in a panned viewport.
    rect = chart._plot_rect()
    point = chart.value_at(rect.center())
    assert point is not None
    mapped = chart._pt(rect, *point).toPoint()
    assert abs(mapped.x() - rect.center().x()) <= 1 and abs(mapped.y() - rect.center().y()) <= 1
    QTest.mouseClick(chart, Qt.MouseButton.MiddleButton, pos=rect.center())
    assert chart._view == (0, 1, 0, 1)
    chart.close()


def test_selection_click_and_precise_edit_undo_redo(make_window, tmp_path):
    win = make_window()
    page = load_page(win, profile())
    chart = page.chart
    assert page.point_combo.count() == 4 and not page.point_apply_btn.isEnabled()
    pos = chart._pt(chart._plot_rect(), chart.x_positions()[1], chart.points[1]).toPoint()
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=pos)
    assert page.point_combo.currentData() == 1
    page.point_velocity.setText("12.123456789123456")
    page.point_sensitivity.setText("1.23456789123456")
    page.point_apply_btn.click()
    p = page._profile
    assert p.horizontal[1].speed == float("12.123456789123456")
    assert p.horizontal[1].multiplier == float("1.23456789123456")
    assert p.vertical[1] == p.horizontal[1]  # identical horizontal/vertical remain linked
    assert page.point_velocity.text() == repr(p.horizontal[1].speed)
    assert chart.points[1] * chart.axis_y_max == pytest.approx(p.horizontal[1].multiplier)
    for ext in ("ccurve", "cc4"):
        path = save_curve(p, tmp_path / ("precise." + ext))
        r = load_curve(path)
        assert r.horizontal[1].speed == p.horizontal[1].speed
        assert r.horizontal[1].multiplier == p.horizontal[1].multiplier
    assert load_curve(tmp_path / "precise.ccurve").ccurve_tail == ["3", "7"]
    page.undo()
    assert page._profile == profile()
    assert float(page.point_velocity.text()) == 10
    page.redo()
    assert page._profile.horizontal[1].speed == float("12.123456789123456")
    win.close()


def test_end_point_expands_axes_and_previous_overlay_remains_true(make_window):
    win = make_window()
    page = win.calibration_page
    a = profile()
    b = deepcopy(a)
    b.horizontal[1].multiplier = 1.4
    page.set_adjusted(a, b, "Compare")
    page.point_combo.setCurrentIndex(3)
    page.point_velocity.setText("65.123456789")
    page.point_sensitivity.setText("4.125")
    page._apply_numeric_point()
    assert page.chart.axis_x_max == pytest.approx(65.123456789)
    assert page.chart.axis_y_max == pytest.approx(4.125)
    assert [x * page.chart.axis_x_max for x in page.chart.previous_xs] == pytest.approx([0, 10, 30])
    assert [y * page.chart.axis_y_max for y in page.chart.previous_points] == pytest.approx([.7, 1.1, 2])
    assert page._profile.horizontal[-1].speed == pytest.approx(65.123456789)
    page.undo()
    assert page._profile == b and page.chart.axis_x_max == pytest.approx(30)
    page.redo()
    assert page._profile.horizontal[-1].speed == pytest.approx(65.123456789)
    win.close()


def test_invalid_values_and_independent_vertical_are_not_silently_changed(make_window):
    win = make_window()
    p = profile()
    p.vertical[1].multiplier = .8
    page = load_page(win, p)
    page.point_combo.setCurrentIndex(2)
    before = deepcopy(page._profile)
    for velocity, gain in [("nan", "1"), ("31", "1"), ("-2", "1"), ("10", "21"), ("x", "1")]:
        page.point_velocity.setText(velocity)
        page.point_sensitivity.setText(gain)
        page._apply_numeric_point()
        assert page._profile == before and not page._undo
    page.point_velocity.setText("11")
    page.point_sensitivity.setText("1.6")
    page._apply_numeric_point()
    assert page._profile.horizontal[1].speed == 11
    assert page._profile.vertical == before.vertical
    win.close()


def test_controls_disabled_during_calibration_and_selection_restored_on_cancel(make_window):
    win = make_window()
    page = load_page(win, profile())
    page.point_combo.setCurrentIndex(2)
    snapshot = page._snapshot()
    page.canvas._active = True
    page._set_curve_controls_enabled(False)
    assert not page.point_combo.isEnabled() and not page.point_apply_btn.isEnabled()
    page.point_velocity.setText("1")
    page.point_sensitivity.setText("1")
    page._apply_numeric_point()
    assert page._profile == profile()
    page.canvas._active = False
    page._restore(snapshot, "Cancelled")
    page._set_curve_controls_enabled(True)
    assert page.point_combo.currentData() == 1
    assert float(page.point_velocity.text()) == 10
    win.close()


def test_large_curve_selected_point_is_visible_and_precise_edit_keeps_metadata(make_window, tmp_path):
    win = make_window()
    points = [CurvePoint(i / 10, .8 + i / 1000, .37, i % 2) for i in range(180)]
    p = CurveProfile("Long", points, deepcopy(points), source_format="ccurve", ccurve_tail=["42", "3"])
    page = load_page(win, p)
    page.point_combo.setCurrentIndex(139)
    selected = page.point_combo.currentData()
    assert selected == 138 and selected in page.chart.dot_indices()
    assert len(page.chart.dot_indices()) == len(points)  # calibration now displays every point
    old = deepcopy(page._profile)
    page.point_velocity.setText("13.812345678912345")
    page.point_sensitivity.setText("1.23456789012345")
    page._apply_numeric_point()
    current = page._profile
    assert current.horizontal[selected].speed == float("13.812345678912345")
    assert current.horizontal[selected].smoothness == old.horizontal[selected].smoothness
    assert current.horizontal[selected].corner == old.horizontal[selected].corner
    assert current.horizontal[:selected] == old.horizontal[:selected]
    assert current.horizontal[selected + 1:] == old.horizontal[selected + 1:]
    assert current.ccurve_tail == ["42", "3"]
    page.point_combo.setCurrentIndex(1)
    page.point_velocity.setText("0.000000000000001")
    page.point_sensitivity.setText("0.000000000000001")
    page._apply_numeric_point()
    path = save_curve(page._profile, tmp_path / "tiny.ccurve")
    assert load_curve(path).horizontal[0].speed == 1e-15
    assert load_curve(path).horizontal[0].multiplier == 1e-15
    win.close()


def test_invalid_field_feedback_is_local_to_editor(make_window):
    win = make_window()
    page = load_page(win, profile())
    page.point_combo.setCurrentIndex(2)
    page.point_velocity.setText("NaN")
    page.point_sensitivity.setText("1")
    page._apply_numeric_point()
    assert not page.point_error.isHidden() and "finite" in page.point_error.text()
    page.point_combo.setCurrentIndex(1)
    assert page.point_error.isHidden()
    win.close()


def test_selecting_point_does_not_change_profile_or_emit_profile_change(make_window):
    win = make_window()
    p = profile()
    p.horizontal[1].speed = 9.123456789123456
    p.horizontal[1].multiplier = 1.234567890123456
    p.vertical[1].speed = 7.123456789123456
    page = load_page(win, p)
    events = []
    page.profile_changed.connect(events.append)
    point = page.chart._pt(page.chart._plot_rect(), page.chart.x_positions()[1], page.chart.points[1]).toPoint()
    QTest.mouseClick(page.chart, Qt.MouseButton.LeftButton, pos=point)
    assert page.point_combo.currentData() == 1
    assert page._profile == p and not page._undo and events == []
    win.close()
