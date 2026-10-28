"""Live calibration graph control points come from captured movement-speed samples."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP_ALIVE: list = []


def _recommendation(points, sample_count=37):
    return {
        "points": list(points),
        "captured_sample_count": sample_count,
        "sensitivity_multiplier": 1.0,
        "confidence": 0.5,
        "title": "Measured curve",
        "reasons": [],
    }


def _page(qapp):
    from fps_coach.ui.pages_setup import CalibrationPage

    page = CalibrationPage(SimpleNamespace(analyzer=None))
    _KEEP_ALIVE.append(page)
    return page


def test_calibration_chart_starts_empty_instead_of_showing_a_preset(qapp):
    page = _page(qapp)

    assert page.chart.points == []
    assert page.chart.xs == []
    assert "waiting for captured movement" in page.curve_status.text().lower()


def test_live_graph_uses_each_data_derived_point_and_reports_the_sample_count(qapp):
    page = _page(qapp)
    points = [(0.0, 0.0), (0.17, 0.11), (0.44, 0.37), (0.76, 0.71), (1.0, 1.0)]

    page._on_live_recommendation({"trial_count": 4}, _recommendation(points, sample_count=91))

    assert page.chart.x_positions() == pytest.approx([x for x, _ in points])
    assert page.chart.points == pytest.approx([y for _, y in points])
    assert len(page.chart.dot_indices()) == len(points)
    status = page.curve_status.text()
    assert "4 completed targets" in status
    assert "91 captured movement samples" in status
    assert "5 data-derived control points" in status


def test_cancelled_session_restores_the_pre_session_curve(qapp):
    page = _page(qapp)
    page._on_session_started()
    page._on_live_recommendation(
        {"trial_count": 1},
        _recommendation([(0.0, 0.0), (0.3, 0.2), (1.0, 1.0)]),
    )
    assert page.chart.points
    assert page.chart._editable is False

    page._on_session_cancelled()

    assert page.chart.points == []
    assert page.chart._editable is True
    assert page._profile is None
    assert "previous curve restored" in page.hint.text().lower()


def test_finished_calibration_undo_returns_to_the_original_curve_not_last_live_frame(qapp):
    page = _page(qapp)
    original_points = list(page.chart.points)
    page._on_session_started()
    page._on_live_recommendation(
        {"trial_count": 2},
        _recommendation([(0.0, 0.0), (0.25, 0.12), (0.6, 0.48), (1.0, 1.0)]),
    )
    final = _recommendation(
        [(0.0, 0.0), (0.12, 0.05), (0.37, 0.24), (0.71, 0.62), (1.0, 1.0)],
        sample_count=150,
    )

    page._on_complete({"trial_count": 12}, final)

    assert len(page.chart.points) == 5
    assert page._profile is not None
    assert page.undo_btn.isEnabled()
    page.undo()
    assert page.chart.points == original_points
    assert page._profile is None
