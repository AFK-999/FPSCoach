"""Editing the calibration curve by hand: drag any dot, and add or delete points from a menu.

Two things are checked here. First, that every dot the chart draws can be dragged — a short curve
shows a dot on every point, so all of them are grabbable, not just the bends. Second, that the
right-click menu only offers what the click allows (add inside the plot, delete on a dot, never
below two points) and that what it does reaches the profile and the Undo history.

No audio and no game are involved: the chart and the calibration page are driven directly.
"""

from __future__ import annotations

import math
import os
from copy import deepcopy
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fps_coach.curve_io import CurvePoint, CurveProfile  # noqa: E402

_KEEP_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def chart(qapp):
    from fps_coach.ui.widgets import CurveChart

    widget = CurveChart()
    widget.resize(640, 320)
    _KEEP_ALIVE.append(widget)
    return widget


def _page(qapp):
    from fps_coach.ui.pages_setup import CalibrationPage

    page = CalibrationPage(SimpleNamespace(analyzer=None))
    page.chart.resize(640, 320)
    _KEEP_ALIVE.append(page)
    return page


def _at(chart, x: float, value: float):
    """The screen position of (x, value) inside the plot."""
    return chart._pt(chart._plot_rect(), x, value).toPoint()


def _press(value: float, x: int = 0, y: int = 0):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )


def _drag_to(chart, index: int, x: float, value: float) -> None:
    """Press on point ``index``, move it to normalized ``(x, value)``, release."""
    start = _at(chart, chart.x_positions()[index], chart.points[index])
    end = _at(chart, x, value)
    chart.mousePressEvent(_press(0, start.x(), start.y()))
    chart.mouseMoveEvent(_mouse_move(end.x(), end.y()))
    chart.mouseReleaseEvent(_mouse_release(end.x(), end.y()))


def _drag(chart, index: int, value: float) -> None:
    """Press on point ``index``, move it vertically, release."""
    _drag_to(chart, index, chart.x_positions()[index], value)


def _mouse_move(x: float, y: float):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )


def _mouse_release(x: float, y: float):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )


def _menu_labels(chart, pos) -> dict[str, object]:
    menu = chart.build_menu(pos)
    _KEEP_ALIVE.append(menu)
    return {action.text(): action for action in menu.actions() if action.text()}


# -- every dot can be dragged -------------------------------------------------

def test_a_short_curve_draws_a_dot_on_every_point(chart) -> None:
    chart.points = [0.0, 0.3, 0.5, 0.78, 1.0]
    assert chart.dot_indices() == [0, 1, 2, 3, 4]


def test_a_long_curve_keeps_its_dots_at_the_bends(chart) -> None:
    """Two hundred points cannot all be dots; the bends are the ones worth grabbing."""
    chart.xs = [i / 199 for i in range(200)]
    chart.points = [0.5 + 0.3 * math.sin(i / 6) for i in range(200)]  # wiggly, but not every point
    assert chart.dot_indices() == chart.handle_indices()
    assert 2 < len(chart.dot_indices()) < 200

    # A highly wiggly curve still has editable dots, but never more than the display limit.
    chart.points = [0.5 + 0.4 * ((i % 2) * 0.5) for i in range(200)]
    assert len(chart.dot_indices()) == 64
    assert chart.dot_indices()[0] == 0 and chart.dot_indices()[-1] == 199
    assert all(i in chart.handle_indices() for i in chart.dot_indices())


def test_a_point_that_is_not_a_bend_can_still_be_dragged(chart) -> None:
    """The complaint this fixes: only the bends used to move, and the rest looked stuck."""
    chart.xs = [i / 4 for i in range(5)]
    chart.points = [0.0, 0.5, 1.0, 0.5, 0.0]  # bends at 0, 2 and 4 only
    assert chart.handle_indices() == [0, 2, 4]

    before = list(chart.points)
    _drag(chart, 1, 0.8)

    assert chart.points[1] > before[1]
    assert chart.points[0] == pytest.approx(before[0])
    assert chart.points[2] == pytest.approx(before[2])


def test_an_interior_dot_can_move_left_and_right(chart) -> None:
    chart.xs = [0.0, 0.5, 1.0]
    chart.points = [0.0, 0.5, 1.0]
    _drag_to(chart, 1, 0.7, 0.5)
    assert chart.x_positions() == pytest.approx([0.0, 0.7, 1.0], abs=0.01)
    assert chart.points[1] == pytest.approx(0.5, abs=0.01)


def test_dragging_an_interior_dot_cannot_cross_its_neighbours(chart) -> None:
    chart.xs = [0.0, 0.3, 0.7, 1.0]
    chart.points = [0.0, 0.3, 0.7, 1.0]
    _drag_to(chart, 1, 0.95, 0.3)
    assert chart.x_positions()[1] <= chart.x_positions()[2]
    assert chart.x_positions()[1] == pytest.approx(0.7)


def test_endpoint_speed_positions_stay_anchored(chart) -> None:
    chart.xs = [0.0, 0.5, 1.0]
    chart.points = [0.0, 0.5, 1.0]
    _drag_to(chart, 0, 0.2, 0.2)
    _drag_to(chart, 2, 0.8, 0.8)
    assert chart.x_positions() == pytest.approx([0.0, 0.5, 1.0], abs=0.01)
    assert chart.points[0] > 0.0 and chart.points[2] < 1.0


def test_a_zero_gain_dot_can_be_dragged_upwards(chart) -> None:
    chart.xs = [0.0, 0.5, 1.0]
    chart.points = [0.0, 0.5, 1.0]
    _drag(chart, 0, 0.2)
    assert chart.points[0] == pytest.approx(0.2, abs=0.01)
    assert chart.points[1] > 0.5  # smooth additive change to the interval, not a stuck zero
    assert chart.points[2] == pytest.approx(1.0)


def test_dragging_a_dot_reports_the_new_curve(chart) -> None:
    seen: list[list[float]] = []
    chart.curve_adjusted.connect(seen.append)
    chart.points = [0.0, 0.5, 1.0]
    _drag(chart, 1, 0.75)
    assert len(seen) == 1
    assert seen[0][1] > 0.5


def test_the_pointer_over_a_dot_shows_the_move_cursor(chart) -> None:
    from PySide6.QtCore import Qt

    chart.points = [0.0, 0.5, 1.0]
    chart.mouseMoveEvent(_mouse_move(*_at(chart, 0.5, 0.5).toTuple()))
    assert chart.cursor().shape() == Qt.CursorShape.SizeAllCursor
    # Away from every dot it goes back to the normal pointer.
    chart.mouseMoveEvent(_mouse_move(*_at(chart, 0.5, 0.05).toTuple()))
    assert chart.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_a_read_only_chart_ignores_drag_and_menu(qapp) -> None:
    from fps_coach.ui.widgets import CurveChart

    widget = CurveChart(editable=False)
    widget.resize(640, 320)
    _KEEP_ALIVE.append(widget)
    widget.points = [0.0, 0.5, 1.0]
    before = list(widget.points)
    _drag(widget, 1, 0.9)
    assert widget.points == before
    assert widget.add_point(_at(widget, 0.4, 0.4)) is None
    assert widget.remove_point(1) is False


# -- adding a point -----------------------------------------------------------

def test_a_right_click_inside_the_plot_adds_a_point_where_it_was(chart) -> None:
    chart.points = [0.0, 1.0]
    seen: list[list[float]] = []
    chart.curve_adjusted.connect(seen.append)

    index = chart.add_point(_at(chart, 0.4, 0.25))

    assert index == 1
    assert len(chart.points) == 3
    assert chart.x_positions()[1] == pytest.approx(0.4, abs=0.01)
    assert chart.points[1] == pytest.approx(0.25, abs=0.01)
    assert seen and seen[-1] == chart.points


def test_an_added_point_keeps_the_other_points_where_they_were(chart) -> None:
    chart.points = [0.0, 0.5, 1.0]  # evenly spaced, no explicit xs
    before = chart.x_positions()
    index = chart.add_point(_at(chart, 0.75, 0.9))
    after = chart.x_positions()
    assert index == 2
    assert after[:index] + after[index + 1:] == pytest.approx(before)
    assert after[index] == pytest.approx(0.75, abs=0.01)


def test_a_click_outside_the_plot_adds_nothing(chart) -> None:
    chart.points = [0.0, 1.0]
    assert chart.add_point(chart._plot_rect().topLeft() - _at_offset()) is None
    assert len(chart.points) == 2


def _at_offset():
    from PySide6.QtCore import QPoint

    return QPoint(40, 40)


def test_adding_stops_at_the_limit(qapp) -> None:
    from fps_coach.ui import widgets

    chart_widget = widgets.CurveChart()
    chart_widget.resize(640, 320)
    _KEEP_ALIVE.append(chart_widget)
    chart_widget.points = [0.5, 0.5]
    chart_widget.xs = [0.0, 1.0]
    original = widgets.MAX_POINTS
    try:
        widgets.MAX_POINTS = 2
        assert chart_widget.add_point(_at(chart_widget, 0.5, 0.5)) is None
        assert len(chart_widget.points) == 2
    finally:
        widgets.MAX_POINTS = original


# -- deleting a point ---------------------------------------------------------

def test_a_point_can_be_deleted_by_index(chart) -> None:
    chart.points = [0.0, 0.4, 0.7, 1.0]
    assert chart.remove_point(1) is True
    assert chart.points == pytest.approx([0.0, 0.7, 1.0])
    assert len(chart.x_positions()) == 3


def test_a_point_can_be_deleted_from_where_it_was_right_clicked(chart) -> None:
    chart.points = [0.0, 0.4, 1.0]
    assert chart.remove_point(pos=_at(chart, 0.5, 0.4)) is True
    assert chart.points == pytest.approx([0.0, 1.0])


def test_right_clicking_away_from_a_dot_deletes_nothing(chart) -> None:
    chart.points = [0.0, 0.4, 1.0]
    assert chart.remove_point(pos=_at(chart, 0.5, 0.9)) is False
    assert len(chart.points) == 3


def test_the_curve_never_drops_below_two_points(chart) -> None:
    chart.points = [0.0, 1.0]
    assert chart.remove_point(0) is False
    assert chart.points == [0.0, 1.0]


def test_deleting_recomputes_the_bends(chart) -> None:
    chart.points = [0.0, 0.9, 0.1, 1.0]
    before = chart.handle_indices()
    chart.remove_point(1)
    assert chart.handle_indices() != before or len(chart.points) == 3
    assert all(0 <= i < len(chart.points) for i in chart.handle_indices())


# -- the menu itself ----------------------------------------------------------

def test_the_menu_offers_add_and_delete(chart) -> None:
    chart.points = [0.0, 0.5, 1.0]
    labels = _menu_labels(chart, _at(chart, 0.5, 0.5))
    assert "Add point here" in labels
    assert "Delete point" in labels


def test_delete_is_only_enabled_over_a_dot(chart) -> None:
    chart.points = [0.0, 0.5, 1.0]
    on_dot = _menu_labels(chart, _at(chart, 0.5, 0.5))
    assert on_dot["Delete point"].isEnabled()
    empty = _menu_labels(chart, _at(chart, 0.25, 0.9))
    assert empty["Delete point"].isEnabled() is False
    assert empty["Add point here"].isEnabled() is True  # adding is still offered


def test_add_is_only_enabled_inside_the_plot(chart) -> None:
    chart.points = [0.0, 0.5, 1.0]
    outside = _menu_labels(chart, chart._plot_rect().topLeft() - _at_offset())
    assert outside["Add point here"].isEnabled() is False


def test_delete_is_disabled_when_only_two_points_are_left(chart) -> None:
    chart.points = [0.0, 1.0]
    labels = _menu_labels(chart, _at(chart, 0.0, 0.0))
    assert labels["Delete point"].isEnabled() is False
    assert labels["Add point here"].isEnabled() is True


def test_the_menu_actions_do_what_they_say(chart) -> None:
    chart.points = [0.0, 0.5, 1.0]
    add = _menu_labels(chart, _at(chart, 0.75, 0.8))["Add point here"]
    add.trigger()
    assert len(chart.points) == 4

    delete = _menu_labels(chart, _at(chart, 0.5, 0.5))["Delete point"]
    delete.trigger()
    assert len(chart.points) == 3


def test_right_click_context_event_opens_the_menu_without_changing_curve(chart) -> None:
    from types import SimpleNamespace

    from PySide6.QtCore import QPoint

    chart.points = [0.0, 0.5, 1.0]
    pos = _at(chart, 0.5, 0.5)
    calls = []

    class FakeMenu:
        def exec(self, global_pos):
            calls.append(("exec", global_pos))

        def deleteLater(self):
            calls.append(("delete", None))

    chart.build_menu = lambda local_pos: (calls.append(("build", local_pos)) or FakeMenu())
    chart.contextMenuEvent(SimpleNamespace(pos=lambda: pos, globalPos=lambda: QPoint(12, 34)))

    assert calls == [("build", pos), ("exec", QPoint(12, 34)), ("delete", None)]
    assert chart.points == [0.0, 0.5, 1.0]


def test_a_read_only_chart_disables_both_context_menu_actions(qapp) -> None:
    from fps_coach.ui.widgets import CurveChart

    widget = CurveChart(editable=False)
    widget.resize(640, 320)
    widget.points = [0.0, 0.5, 1.0]
    _KEEP_ALIVE.append(widget)
    labels = _menu_labels(widget, _at(widget, 0.5, 0.5))
    assert labels["Add point here"].isEnabled() is False
    assert labels["Delete point"].isEnabled() is False


def test_the_context_menu_uses_the_active_theme(chart) -> None:
    from fps_coach.ui.styles import active_palette

    menu = chart.build_menu(_at(chart, 0.5, 0.5))
    _KEEP_ALIVE.append(menu)
    palette = active_palette()
    style = menu.styleSheet()
    assert palette["surface2"] in style and palette["text"] in style
    assert palette["raised"] in style and palette["line2"] in style


# -- the page: profile and history -------------------------------------------

def _profile(gains: list[float], top: float = 1000.0) -> CurveProfile:
    n = len(gains)
    points = [CurvePoint(speed=top * i / (n - 1), multiplier=g) for i, g in enumerate(gains)]
    return CurveProfile(name="Test", horizontal=points, vertical=deepcopy(list(points)))


def _loaded_page(monkeypatch, tmp_path, qapp):
    """A calibration page whose profile is the curve on the chart."""
    from fps_coach.curve_io import load_curve, save_curve
    from PySide6.QtWidgets import QFileDialog

    page = _page(qapp)
    path = tmp_path / "curve.cc4"
    save_curve(_profile([0.5, 1.0, 1.5, 2.0, 2.5]), path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    page._import_curve()
    load_curve(path)
    return page


def test_adding_a_point_on_the_page_updates_both_axes_and_can_be_undone(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    points_before = list(page.chart.points)
    xs_before = list(page.chart.x_positions())
    multipliers_before = [p.multiplier for p in page._profile.horizontal]

    page.chart.add_point(_at(page.chart, 0.55, 0.9))
    assert len(page.chart.points) == len(points_before) + 1
    assert len(page._profile.horizontal) == len(page._profile.vertical) == 6
    added = min(page._profile.horizontal, key=lambda p: abs(p.speed / 1000 - 0.55))
    assert added.multiplier > 2.0  # the clicked gain is inserted into the exportable profile

    page.undo()

    assert page.chart.points == pytest.approx(points_before)
    assert page.chart.x_positions() == pytest.approx(xs_before)
    assert [p.multiplier for p in page._profile.horizontal] == pytest.approx(multipliers_before)
    assert len(page._profile.horizontal) == len(page._profile.vertical) == 5

    page.redo()
    assert len(page.chart.points) == 6
    assert len(page._profile.horizontal) == len(page._profile.vertical) == 6


def test_deleting_a_point_on_the_page_removes_it_from_both_axes_and_can_be_undone(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    points_before = list(page.chart.points)
    page.chart.remove_point(2)
    assert len(page.chart.points) == 4
    assert len(page._profile.horizontal) == len(page._profile.vertical) == 4
    page.undo()
    assert page.chart.points == pytest.approx(points_before)
    assert len(page._profile.horizontal) == len(page._profile.vertical) == 5

    page.redo()
    assert len(page.chart.points) == 4
    assert len(page._profile.horizontal) == len(page._profile.vertical) == 4


def test_horizontal_drag_moves_the_exported_speed_and_undo_restores_it(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    _drag_to(page.chart, 2, 0.6, page.chart.points[2])
    assert page._profile.horizontal[2].speed == pytest.approx(600.0, abs=10.0)
    assert page._profile.vertical[2].speed == pytest.approx(600.0, abs=10.0)

    page.undo()

    assert page._profile.horizontal[2].speed == pytest.approx(500.0)
    assert page.chart.x_positions()[2] == pytest.approx(0.5)


def test_adding_a_point_changes_the_curve_the_page_would_export(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    before = [p.multiplier for p in page._profile.horizontal]
    page.chart.add_point(_at(page.chart, 0.45, 1.0))  # a big lift between two points
    after = [p.multiplier for p in page._profile.horizontal]
    assert after != before
    assert max(after) > max(before)


def test_export_contains_a_manually_added_control_point(monkeypatch, tmp_path, qapp) -> None:
    from fps_coach.curve_io import load_curve
    from PySide6.QtWidgets import QFileDialog

    page = _loaded_page(monkeypatch, tmp_path, qapp)
    destination = tmp_path / "edited.cc4"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(destination), ""))
    )
    page.chart.add_point(_at(page.chart, 0.45, 0.9))

    page._save()
    exported = load_curve(destination)

    assert len(exported.horizontal) == len(exported.vertical) == 6
    assert any(point.speed == pytest.approx(450.0, abs=5.0) for point in exported.horizontal)


def test_the_page_says_the_point_count_changed(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    page.chart.add_point(_at(page.chart, 0.5, 0.9))
    assert "point count changed" in page.hint.text().lower()
    assert "6 points" in page.hint.text()
    assert "Export curve" in page.hint.text()


def test_dragging_a_zero_gain_dot_changes_the_exported_curve(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    before = [point.multiplier for point in page._profile.horizontal]
    _drag(page.chart, 0, 0.2)
    after = [point.multiplier for point in page._profile.horizontal]
    assert after != before


def test_a_plain_drag_still_reports_a_plain_edit(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    _drag(page.chart, 2, min(1.0, page.chart.points[2] + 0.2))
    assert "Curve edited" in page.hint.text()


def test_undo_after_adding_brings_back_the_same_point_count(monkeypatch, tmp_path, qapp) -> None:
    page = _loaded_page(monkeypatch, tmp_path, qapp)
    page.chart.add_point(_at(page.chart, 0.3, 0.2))
    page.chart.add_point(_at(page.chart, 0.8, 0.6))
    assert len(page.chart.points) == 7
    page.undo()
    assert len(page.chart.points) == 6
    page.undo()
    assert len(page.chart.points) == 5


def test_the_card_points_at_the_new_gestures(qapp) -> None:
    page = _page(qapp)
    subscriptions = page.findChildren(type(page.hint))
    texts = " ".join(w.text() for w in subscriptions if hasattr(w, "text"))
    assert "Drag" in texts and "right-click" in texts.lower()
