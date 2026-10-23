"""Calibration chart: imported custom curves are drawn point for point, not as a five-point summary.

Each point sits at its own speed position (0..1 of the curve's top speed). Drag edits change only
the point that was dragged, and undo restores the long curve exactly.
"""

from __future__ import annotations

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
    """Uneven speeds over a long range, with gains that wander, so every point matters."""
    horizontal = [
        CurvePoint(speed=round(top * (i / (n - 1)) ** 1.7, 2), multiplier=round(0.4 + 0.5 * ((i * 7) % 11) / 10, 3))
        for i in range(n)
    ]
    return CurveProfile(name="Long range", horizontal=horizontal, vertical=deepcopy(horizontal))


def _import(page, monkeypatch, tmp_path, profile: CurveProfile):
    from PySide6.QtWidgets import QFileDialog

    path = tmp_path / "long.cc4"
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


def _screen_y_for(chart, value: float) -> float:
    rect = chart._plot_rect()
    return rect.bottom() - rect.height() * value


# -- the chart's point handling -------------------------------------------------------

def test_points_are_sorted_clamped_and_not_resampled() -> None:
    from fps_coach.ui.widgets import _curve_points

    xs, ys = _curve_points([(1.2, 2.0), (0.0, -1.0), (0.5, 0.5), (0.25, 0.3)])
    assert xs == [0.0, 0.25, 0.5, 1.0]
    assert ys == [0.0, 0.3, 0.5, 1.0]


def test_too_few_points_fall_back_to_the_default_shape() -> None:
    from fps_coach.ui.widgets import _curve_points

    assert _curve_points([]) == ([0.0, 0.25, 0.5, 0.75, 1.0],) * 2
    assert _curve_points([(0.0, 0.5)]) == ([0.0, 0.25, 0.5, 0.75, 1.0],) * 2


def test_handles_are_drawn_only_for_short_curves(qapp) -> None:
    from fps_coach.ui.widgets import DOT_LIMIT, CurveChart

    chart = CurveChart()
    for n in (2, 5, DOT_LIMIT, DOT_LIMIT + 1, 60):
        chart.points = [0.5] * n
        assert chart.handles_visible() == (n <= DOT_LIMIT), n


def test_positions_fall_back_to_even_spacing_when_they_do_not_match(qapp) -> None:
    from fps_coach.ui.widgets import CurveChart

    chart = CurveChart()
    chart.points = [0.0, 0.5, 1.0]
    chart.xs = [0.0, 0.1]  # wrong length: ignored
    assert chart.x_positions() == [0.0, 0.5, 1.0]


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


def test_long_curve_points_are_distinct_and_have_no_handle_dots(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    _import(page, monkeypatch, tmp_path, _long_profile())
    xs = page.chart.x_positions()
    assert len(set(xs)) == 60
    assert not page.chart.handles_visible()


def test_imported_short_curve_still_shows_handles(qapp, monkeypatch, tmp_path) -> None:
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

def _drag_point(page, index: int, new_value: float) -> None:
    """A real press / move / release on one point, through the chart's mouse handlers."""
    chart = page.chart
    x0, y0 = _screen_point(chart, index, chart.points[index])
    x1, y1 = _screen_point(chart, index, new_value)
    chart.mousePressEvent(_mouse("press", x0, y0))
    chart.mouseMoveEvent(_mouse("move", x1, y1))
    chart.mouseReleaseEvent(_mouse("release", x1, y1))


def test_dragging_one_point_on_a_long_curve_changes_only_that_point(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    _import(page, monkeypatch, tmp_path, _long_profile())
    page.chart.resize(640, 320)
    before = [p.multiplier for p in page._profile.horizontal]
    start = page.chart.points[30]
    target = min(1.0, start + 0.2)

    _drag_point(page, 30, target)

    after = [p.multiplier for p in page._profile.horizontal]
    changed = [i for i in range(60) if abs(after[i] - before[i]) > 1e-9]
    assert changed == [30]
    assert page.chart.points[30] == pytest.approx(target, abs=0.01)
    assert after[30] / before[30] == pytest.approx(page.chart.points[30] / start, rel=1e-6)


def test_undo_restores_the_long_curve_exactly(qapp, monkeypatch, tmp_path) -> None:
    page = _page(qapp)
    _import(page, monkeypatch, tmp_path, _long_profile())
    page.chart.resize(640, 320)
    points_before = list(page.chart.points)
    xs_before = list(page.chart.x_positions())
    multipliers_before = [p.multiplier for p in page._profile.horizontal]

    _drag_point(page, 12, min(1.0, page.chart.points[12] + 0.15))
    assert page.chart.points != points_before

    page.undo()

    assert page.chart.points == points_before
    assert page.chart.x_positions() == xs_before
    assert [p.multiplier for p in page._profile.horizontal] == multipliers_before


def test_edit_after_an_overlay_still_changes_only_the_dragged_point(qapp, monkeypatch, tmp_path) -> None:
    """The overlay's shared scale must match the profile's, or a drag would move its neighbours."""
    page = _page(qapp)
    base = _import(page, monkeypatch, tmp_path, _long_profile(top=5000.0))
    adjusted = deepcopy(base)
    adjusted.horizontal = [
        CurvePoint(speed=p.speed * (1.0 if i < 30 else 1.2), multiplier=p.multiplier)
        for i, p in enumerate(base.horizontal)
    ]  # a larger top speed than the base, so the two curves use a shared scale
    page.set_adjusted(base, adjusted, "AI adjustment")
    page.chart.resize(640, 320)

    prev_xs, _ = normalized_overlay(base, adjusted)
    assert page.chart.previous_xs == pytest.approx([x for x, _ in prev_xs])
    assert len(page.chart.previous_points) == 60

    before = [p.multiplier for p in page._profile.horizontal]
    _drag_point(page, 40, min(1.0, page.chart.points[40] + 0.1))
    after = [p.multiplier for p in page._profile.horizontal]
    assert [i for i in range(60) if abs(after[i] - before[i]) > 1e-9] == [40]


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
