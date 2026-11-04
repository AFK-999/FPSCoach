"""Smooth, vertically fitted zoom and true CC3/CC4 curve endpoints."""
from __future__ import annotations

import math

import pytest
from fps_coach.ui.widgets import CurveChart
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wheel(chart, pos, *, angle=0, pixels=0):
    event = QWheelEvent(QPointF(pos), QPointF(chart.mapToGlobal(pos)), QPoint(0, pixels), QPoint(0, angle),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(chart, event)
    return event


@pytest.mark.parametrize("style,aligned", [("cc3", True), ("cc3", False), ("cc4", True), ("cc4", False)])
def test_curve_ends_at_real_last_control_even_when_view_or_other_curve_extends(app, style, aligned):
    chart = CurveChart(editable=aligned, interpolate_points=aligned)
    chart.resize(500, 260)
    chart.points, chart.xs = [.3, .85, .45], [0, .25, .6]
    chart.set_curve_weights([.1, .8, .3])
    chart.set_previous([.2, .5, .7], [0, .3, .9])
    chart.set_graph_style(style)
    chart.show()
    app.processEvents()
    chart._view = (0, 2.5, 0, 1.2)
    rect = chart._plot_rect()
    current = chart._curve_path(rect, chart.points, chart.x_positions(), chart.weights)
    previous = chart._curve_path(rect, chart.previous_points, chart.previous_xs)
    for path, x, y in [(current, .6, .45), (previous, .9, .7)]:
        end = chart._pt(rect, x, y)
        assert path.currentPosition().x() == pytest.approx(end.x())
        assert path.currentPosition().y() == pytest.approx(end.y())
        assert path.boundingRect().right() == pytest.approx(end.x())
    chart.close()


@pytest.mark.parametrize("style", ["cc3", "cc4"])
def test_plot_zoom_is_smooth_bottom_anchored_and_fits_both_curves(app, style):
    chart = CurveChart(editable=True, interpolate_points=True)
    chart.resize(500, 260)
    chart.points, chart.xs = [.3, .72, .4], [0, .5, 1]
    chart.set_curve_weights([.3, .7, .5])
    chart.set_previous([.2, 1, .55], [0, .45, .8])
    chart.set_graph_style(style)
    chart.show()
    app.processEvents()
    rect = chart._plot_rect()
    original = (list(chart.points), chart.x_positions(), list(chart.weights), list(chart.previous_points))
    # Scroll outward and back in at varying vertical positions: baseline stays
    # fixed and the taller previous curve never exits the viewport.
    for angle in [-120, -120, 120, 120, 120, 120]:
        wheel(chart, QPoint(rect.center().x(), rect.top() + 3), angle=angle)
        assert chart._view[2] == 0
        assert chart._view[3] >= 1
        for y in chart.points + chart.previous_points:
            screen_y = chart._pt(rect, .5, y).y()
            assert rect.top() - 1e-7 <= screen_y <= rect.bottom() + 1e-7
    assert (chart.points, chart.x_positions(), chart.weights, chart.previous_points) == original
    chart.close()


def test_fractional_and_touchpad_deltas_zoom_without_snapping(app):
    chart = CurveChart()
    chart.resize(500, 260)
    chart.points, chart.xs = [.25, .8], [0, 1]
    chart.show()
    app.processEvents()
    pos = chart._plot_rect().center()
    wheel(chart, pos, angle=15)
    assert chart._view[1] - chart._view[0] == pytest.approx(.9 ** (15 / 120))
    wheel(chart, pos, pixels=15)
    assert chart._view[1] - chart._view[0] == pytest.approx(.9 ** (30 / 120))
    assert all(math.isfinite(v) for v in chart._view)
    chart.close()


def test_vertical_zoom_refits_after_explicit_pan_horizontal_axis_is_independent(app):
    chart = CurveChart()
    chart.resize(500, 260)
    chart.points, chart.xs = [.25, .8, .4], [0, .5, 1]
    chart.show()
    app.processEvents()
    rect = chart._plot_rect()
    chart._view = (0, 1, .7, 1.1)  # a deliberate vertical pan cropped the data
    wheel(chart, QPoint(rect.center().x(), rect.bottom() + 5), angle=120)
    assert chart._view[2:] == (.7, 1.1)  # horizontal-only operation
    before = chart._view[:2]
    wheel(chart, QPoint(rect.left() - 4, rect.center().y()), angle=120)
    assert chart._view[:2] == before
    assert chart._view[2] <= .25 and chart._view[3] >= .8
    chart.reset_view()
    assert chart._view == (0, 1, 0, 1)
    chart.close()
