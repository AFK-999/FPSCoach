"""Calibration chart: the line runs through every point, and handles sit at the curve's bends.

A custom curve of any length or shape is drawn point for point. The handles a user grabs are the
bends the curve needs, so a straight run has two and a wiggly curve has many. A drag moves one
handle and scales only the points between its neighbouring handles.
"""

from __future__ import annotations

import math
import os
from copy import deepcopy
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from fps_coach.curve_adjust import normalized_overlay  # noqa: E402
from fps_coach.curve_io import CurvePoint, CurveProfile, chart_points, load_curve, save_curve  # noqa: E402

_KEEP_ALIVE: list = []  # Qt widgets are torn down in a fixed order when kept until exit


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _page(qapp):
    from fps_coach.ui.pages_setup import CalibrationPage

    page = CalibrationPage(SimpleNamespace(analyzer=None))
    _KEEP_ALIVE.append(page)
    return page


def _long_profile(n: int = 60, top: float = 5000.0) -> CurveProfile:
    """Uneven speeds over a long range, with gains that wander, so the curve has many bends."""
    horizontal = [
        CurvePoint(speed=round(top * (i / (n - 1)) ** 1.7, 2), multiplier=round(0.4 + 0.5 * ((i * 7) % 11) / 10, 3))
        for i in range(n)
    ]
    return CurveProfile(name="Long range", horizontal=horizontal, vertical=deepcopy(horizontal))


def _profile_from_gains(gains: list[float], top: float = 1000.0) -> CurveProfile:
    n = len(gains)
    horizontal = [CurvePoint(speed=top * i / (n - 1), multiplier=g) for i, g in enumerate(gains)]
    return CurveProfile(name="Test", horizontal=horizontal, vertical=deepcopy(horizontal))


def _import(page, monkeypatch, tmp_path, profile: CurveProfile):
    from PySide6.QtWidgets import QFileDialog

    path = tmp_path / "curve.cc4"
    save_curve(profile, path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    page._import_curve()
    return load_curve(path)


def _mouse(kind, x: float, y: float):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    if kind == "press":
        return QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
    if kind == "move":
        return QMouseEvent(
            QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )


def _screen_point(chart, index: int, value: float):
    rect = chart._plot_rect()
    pt = chart._pt(rect, chart.x_positions()[index], value)
    return pt.x(), pt.y()


def _drag_handle(page, index: int, new_value: float) -> None:
    """A real press / move / release on one handle, through the chart's mouse handlers."""
    chart = page.chart
    x0, y0 = _screen_point(chart, index, chart.points[index])
    x1, y1 = _screen_point(chart, index, new_value)
    chart.mousePressEvent(_mouse("press", x0, y0))
    chart.mouseMoveEvent(_mouse("move", x1, y1))
    chart.mouseReleaseEvent(_mouse("release", x1, y1))


def _middle_handle(chart) -> tuple[int, int, int]:
    """(previous handle, this handle, next handle) around the middle of the chart's handles."""
    handles = chart.handle_indices()
    k = len(handles) // 2
    return handles[k - 1], handles[k], handles[k + 1]


# -- the chart's point handling -------------------------------------------------------

def test_points_are_sorted_clamped_and_not_resampled() -> None:
    from fps_coach.ui.widgets import _curve_points

    xs, ys = _curve_points([(1.2, 2.0), (0.0, -1.0), (0.5, 0.5), (0.25, 0.3)])
    assert xs == [0.0, 0.25, 0.5, 1.0]
    assert ys == [0.0, 0.3, 0.5, 1.0]


def test_points_with_equal_speed_keep_their_order() -> None:
    """A vertical step (two points at one speed) must not swap, or drag edits would land on the wrong point."""
    from fps_coach.ui.widgets import _curve_points

    xs, ys = _curve_points([(0.0, 0.1), (0.5, 0.9), (0.5, 0.2), (1.0, 0.5)])
    assert xs == [0.0, 0.5, 0.5, 1.0]
    assert ys == [0.1, 0.9, 0.2, 0.5]


def test_too_few_points_fall_back_to_the_default_shape() -> None:
    from fps_coach.ui.widgets import _curve_points

    assert _curve_points([]) == ([0.0, 0.25, 0.5, 0.75, 1.0],) * 2
    assert _curve_points([(0.0, 0.5)]) == ([0.0, 0.25, 0.5, 0.75, 1.0],) * 2


def test_positions_fall_back_to_even_spacing_when_they_do_not_match(qapp) -> None:
    from fps_coach.ui.widgets import CurveChart

    chart = CurveChart()
    chart.points = [0.0, 0.5, 1.0]
    chart.xs = [0.0, 0.1]  # wrong length: ignored
    assert chart.x_positions() == [0.0, 0.5, 1.0]


def test_handles_follow_the_bends_not_the_length(qapp) -> None:
    from fps_coach.ui.widgets import HANDLE_DOT_LIMIT, CurveChart

    chart = CurveChart()
    _KEEP_ALIVE.append(chart)

    # A straight run of any length needs only its two ends.
    chart.xs = [i / 59 for i in range(60)]
    chart.points = [i / 59 for i in range(60)]
    assert chart.handle_indices() == [0, 59]
    assert chart.handles_visible()

    # A wiggly curve needs many handles, but not every point.
    chart.xs = [i / 199 for i in range(200)]
    chart.points = [0.5 + 0.4 * math.sin(i / 8) for i in range(200)]
    assert 2 < len(chart.handle_indices()) < 200

    # Zig-zag: every point is a bend, so there are too many handles to draw as dots.
    chart.xs = [i / 199 for i in range(200)]
    chart.points = [float(i % 2) for i in range(200)]
    assert len(chart.handle_indices()) > HANDLE_DOT_LIMIT
    assert not chart.handles_visible()


def test_two_and_five_point_curves_show_their_handles(qapp) -> None:
    from fps_coach.ui.widgets import CurveChart

    chart = CurveChart()
    _KEEP_ALIVE.append(chart)
    chart.points = [0.0, 1.0]
    assert chart.handle_indices() == [0, 1] and chart.handles_visible()
    chart.points = [0.0, 0.3, 0.55, 0.78, 1.0]
    assert chart.handles_visible()


def test_handles_are_cached_until_the_curve_is_replaced(qapp) -> None:
    from fps_coach.ui.widgets import CurveChart

    chart = CurveChart()
    _KEEP_ALIVE.append(chart)
    chart.points = [0.0, 1.0, 0.0, 1.0, 0.0]
    first = chart.handle_indices()
    chart.points[2] = 0.5  # an in-place edit (as a drag does) keeps the same handles
    assert chart.handle_indices() is first
    chart.points = [0.0, 0.5, 1.0]  # a new curve recomputes them
    assert chart.handle_indices() == [0, 2]


# -- importing a long custom curve ----------------------------------------------------

def test_imported_long_curve_keeps_every_point(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    loaded = _import(page, monkeypatch, tmp_path, _long_profile())
    top = max(p.speed for p in loaded.horizontal)
    expected = chart_points(loaded)

    assert len(page.chart.points) == len(loaded.horizontal) == 60
    assert page.chart.x_positions() == pytest.approx([p.speed / top for p in loaded.horizontal])
    assert page.chart.points == pytest.approx([y for _, y in expected])
    assert page.chart.x_positions()[-1] == pytest.approx(1.0)


def test_long_curve_has_handles_at_its_bends(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    _import(page, monkeypatch, tmp_path, _long_profile())
    handles = page.chart.handle_indices()
    assert 2 < len(handles) < 60
    assert handles[0] == 0 and handles[-1] == 59
    assert len(set(page.chart.x_positions())) == 60


def test_imported_short_curve_shows_its_handles(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    _import(page, monkeypatch, tmp_path, _long_profile(n=5))
    assert len(page.chart.points) == 5
    assert page.chart.handles_visible()


def test_chart_renders_the_long_curve(qapp) -> None:
    from fps_coach.ui.widgets import CurveChart

    chart = CurveChart()
    _KEEP_ALIVE.append(chart)
    xs, ys = [i / 59 for i in range(60)], [0.4 + 0.5 * ((i * 7) % 11) / 10 for i in range(60)]
    chart.xs, chart.points = xs, [y / max(ys) for y in ys]
    chart.resize(640, 320)
    image = chart.grab().toImage()
    assert image.width() == 640 and image.height() == 320


# -- editing a long curve -------------------------------------------------------------

def test_dragging_a_handle_changes_only_the_points_between_its_neighbours(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    _import(page, monkeypatch, tmp_path, _long_profile())
    page.chart.resize(640, 320)
    prev_h, h, next_h = _middle_handle(page.chart)
    before = [p.multiplier for p in page._profile.horizontal]
    start = page.chart.points[h]
    target = min(1.0, start + 0.2)

    _drag_handle(page, h, target)

    after = [p.multiplier for p in page._profile.horizontal]
    changed = [i for i in range(60) if abs(after[i] - before[i]) > 1e-9]
    assert h in changed
    assert all(prev_h < i < next_h for i in changed), changed
    assert page.chart.points[h] == pytest.approx(target, abs=0.01)
    assert after[h] / before[h] == pytest.approx(page.chart.points[h] / start, rel=1e-6)


def test_points_between_handles_move_smoothly_not_in_steps(qapp) -> None:
    """Dragging a handle scales the points between it and its neighbours by a straight-line factor."""
    from fps_coach.ui.widgets import CurveChart

    chart = CurveChart()
    _KEEP_ALIVE.append(chart)
    chart.xs = [i / 10 for i in range(11)]
    chart.points = [0.2 + 0.6 * abs(math.sin(i / 2.5)) for i in range(11)]  # bends at several points
    chart.resize(400, 200)
    handles = chart.handle_indices()
    h = handles[len(handles) // 2]
    base = list(chart.points)
    chart._drag_base = list(base)
    chart._move_handle(h, min(1.0, base[h] * 1.1))
    factors = [after / before for after, before in zip(chart.points, base)]
    lo, hi = handles[len(handles) // 2 - 1], handles[len(handles) // 2 + 1]
    for i in range(lo + 1, hi):
        if i != h:
            assert factors[i] != pytest.approx(1.0, abs=1e-6)  # between handles: moved
    assert factors[lo] == pytest.approx(1.0) and factors[hi] == pytest.approx(1.0)  # neighbours: unmoved


def test_undo_restores_the_long_curve_exactly(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    _import(page, monkeypatch, tmp_path, _long_profile())
    page.chart.resize(640, 320)
    points_before = list(page.chart.points)
    xs_before = list(page.chart.x_positions())
    multipliers_before = [p.multiplier for p in page._profile.horizontal]

    _prev, h, _next = _middle_handle(page.chart)
    _drag_handle(page, h, min(1.0, page.chart.points[h] + 0.15))
    assert page.chart.points != points_before

    page.undo()

    assert page.chart.points == points_before
    assert page.chart.x_positions() == xs_before
    assert [p.multiplier for p in page._profile.horizontal] == multipliers_before


def test_edit_after_an_overlay_changes_only_the_points_between_its_handles(qapp, monkeypatch, tmp_path) -> None:
    """The overlay's shared scale must match the profile's, or a drag would move the wrong points."""
    page = _page(qapp)
    base = _import(page, monkeypatch, tmp_path, _long_profile(top=5000.0))
    adjusted = deepcopy(base)
    adjusted.horizontal = [
        CurvePoint(speed=p.speed * (1.0 if i < 30 else 1.2), multiplier=p.multiplier)
        for i, p in enumerate(base.horizontal)
    ]  # a larger top speed than the base, so the two curves use a shared scale
    page.set_adjusted(base, adjusted, "AI adjustment")
    page.chart.resize(640, 320)

    prev_pts, _cur = normalized_overlay(base, adjusted)
    assert page.chart.previous_xs == pytest.approx([x for x, _ in prev_pts])
    assert len(page.chart.previous_points) == 60

    prev_h, h, next_h = _middle_handle(page.chart)
    before = [p.multiplier for p in page._profile.horizontal]
    _drag_handle(page, h, min(1.0, page.chart.points[h] + 0.1))
    after = [p.multiplier for p in page._profile.horizontal]
    changed = [i for i in range(60) if abs(after[i] - before[i]) > 1e-9]
    assert h in changed
    assert all(prev_h < i < next_h for i in changed), changed


def test_overlay_keeps_both_curves_at_their_real_positions(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    base = _import(page, monkeypatch, tmp_path, _long_profile(top=5000.0))
    adjusted = deepcopy(base)
    adjusted.horizontal = [CurvePoint(speed=p.speed, multiplier=p.multiplier * 1.1) for p in base.horizontal]
    page.set_adjusted(base, adjusted, "AI adjustment")

    prev_pts, cur_pts = normalized_overlay(base, adjusted)  # (previous, current), as set_adjusted uses them
    assert page.chart.x_positions() == pytest.approx([x for x, _ in cur_pts])
    assert page.chart.previous_xs == pytest.approx([x for x, _ in prev_pts])
    assert len(page.chart.points) == len(page.chart.previous_points) == 60


# -- the recommendation curves --------------------------------------------------------

SEVEN_POINTS = [(0.0, 0.0), (0.08, 0.05), (0.2, 0.14), (0.4, 0.33), (0.62, 0.55), (0.82, 0.76), (1.0, 1.0)]


def test_recommendation_shows_all_seven_points_on_calibration(qapp) -> None:
    page = _page(qapp)
    page.apply_recommendation({"points": SEVEN_POINTS, "sensitivity_multiplier": 1.0})
    assert len(page.chart.points) == 7
    assert page.chart.x_positions() == pytest.approx([x for x, _ in SEVEN_POINTS])
    assert page.chart.points == pytest.approx([y for _, y in SEVEN_POINTS])


def test_recommendation_shows_all_seven_points_on_the_recommendation_tab(qapp) -> None:
    from fps_coach.backend import CoreBackend
    from fps_coach.ui.pages_setup import RecommendationPage

    page = RecommendationPage(CoreBackend())
    _KEEP_ALIVE.append(page)
    page.update_result({"trial_count": 12}, {"points": SEVEN_POINTS, "sensitivity_multiplier": 1.0,
                                             "title": "t", "reasons": []})
    assert len(page.chart.points) == 7
    assert page.chart.x_positions() == pytest.approx([x for x, _ in SEVEN_POINTS])
    page.clear_result()
    assert page.chart.xs is None
