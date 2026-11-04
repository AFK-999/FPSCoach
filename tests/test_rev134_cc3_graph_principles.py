"""CC3-style spline behavior, preservation, and truthful limitations."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.curve_io import CurvePoint, CurveProfile, load_curve, save_curve
from fps_coach.curve_spline import cc3_spline
from fps_coach.ui.widgets import CurveChart
from PySide6.QtWidgets import QFileDialog, QMessageBox

pytest_plugins = ["test_rev132_numerical_curve_axes"]


def test_cc3_four_controls_are_a_cubic_bezier_not_a_polyline():
    # Cubic B-spline with four points is a cubic Bézier; the interior dots are
    # influence controls, rather than points guaranteed to lie on the line.
    points = [(0.0, .1), (.2, .9), (.8, .9), (1.0, .1)]
    curve = cc3_spline(points, samples_per_span=32)
    assert curve[0] == points[0]
    assert curve[-1] == points[-1]
    assert curve[16] == pytest.approx((.5, .7))
    assert points[1] not in curve and points[2] not in curve


def test_cc3_preview_matches_reference_example_to_visual_tolerance():
    # Public CC3 screenshot/profile: https://whaamkabaam.com/library/6a29e15ab7264177
    # Coarse screen-derived readings only; this is NOT verification of driver math.
    pts = [(6.0554, .74797), (10.4667, 1.61065), (70.1765, 1.72567), (80.1018, 1.72567)]
    curve = cc3_spline(pts, samples_per_span=1024)
    for x, observed in [(10, 1.07), (20, 1.36), (40, 1.60), (60, 1.70)]:
        actual = min(curve, key=lambda p: abs(p[0] - x))[1]
        assert actual == pytest.approx(observed, abs=.08)


def test_all_graphs_use_cc3_description_and_no_weight_editing(make_window):
    win = make_window()
    pts = [CurvePoint(0, .5, .1), CurvePoint(10, 2, .9), CurvePoint(20, 1, .3), CurvePoint(50, 1.5, .7)]
    p = CurveProfile("test", pts, deepcopy(pts), source_format="cc4")
    win.calibration_page.set_adjusted(p, deepcopy(p), "Preview")
    win.recommendation_page.show_curve_profile(p)
    win.chat_page._show_preview(p, deepcopy(p), "Preview")
    win.dynamic_curve.base = p
    win.dynamic_curve.proposed = deepcopy(p)
    win.dynamic_curve.changed.emit()
    charts = [win.calibration_page.chart, win.recommendation_page.chart, win.chat_page.quick_preview,
              win.recommendation_page.dynamic_curve_panel.chart,
              win.training_page.dynamic_curve_panel.chart, win.training_page.dynamic_curve_monitor.chart]
    assert all("cc3" in c.accessibleDescription().lower() for c in charts)
    chart = win.calibration_page.chart
    point = chart._pt(chart._plot_rect(), chart.x_positions()[1], chart.points[1]).toPoint()
    menu = chart.build_menu(point)
    assert not any("weight" in action.text().lower() for action in menu.actions())
    menu.deleteLater()
    win.close()


def test_cc3_six_line_and_extra_setting_rows_roundtrip_without_data_loss(tmp_path):
    rows = ["0 10 20 40", "0.5 0.9 1.1 1.2", "0 1 0 0",
            "0 10 20 40", "0.5 0.9 1.1 1.2", "0 1 0 0"]
    tail = ["11 2 0", "0", "400 800", "1", "123", "0 0", "5"]
    path = tmp_path / "settings.ccurve"
    path.write_text("\n".join(rows + tail) + "\n")
    profile = load_curve(path)
    assert profile.ccurve_tail == tail
    assert profile.horizontal[1].corner is True
    output = save_curve(profile, tmp_path / "saved.ccurve")
    assert output.read_text().splitlines()[6:] == tail
    reloaded = load_curve(output)
    assert reloaded.horizontal == profile.horizontal and reloaded.ccurve_tail == tail
    short = tmp_path / "short.ccurve"
    short.write_text("\n".join(rows) + "\n")
    assert len(save_curve(load_curve(short), tmp_path / "short-saved.ccurve").read_text().splitlines()) == 6


def test_export_defaults_to_cc3_and_warns_when_cc4_weights_are_lost(make_window, tmp_path, monkeypatch):
    win = make_window()
    page = win.calibration_page
    points = [CurvePoint(0, .8, .2), CurvePoint(20, 1.5, .9)]
    page._profile = CurveProfile("from CC4", points, deepcopy(points), source_format="cc4")
    captured = []
    target = tmp_path / "converted.ccurve"
    def choose(_parent, _title, default_name, filters):
        captured.append((default_name, filters))
        return str(target), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    page._save()
    assert captured[0][0].endswith(".ccurve") and captured[0][1].startswith("Custom Curve 3")
    assert "weights and settings cannot" in page.hint.text().lower()
    assert target.exists()
    win.close()


def test_control_polygon_has_no_weight_action(qapp):
    chart = CurveChart()
    chart.points = [.5, .7, .9]
    menu = chart.build_menu(chart._pt(chart._plot_rect(), .5, .7).toPoint())
    assert [a.text() for a in menu.actions() if a.text()] == ["Add point here", "Delete point"]
    menu.deleteLater()
    chart.close()
