"""Switchable, non-destructive CC3/CC4-style previews and weight editing."""
from __future__ import annotations

from copy import deepcopy

from fps_coach.curve_io import CurvePoint, CurveProfile, load_curve, save_curve
from fps_coach.ui.widgets import CurveChart
from PySide6.QtWidgets import QFileDialog, QMessageBox

pytest_plugins = ["test_rev132_numerical_curve_axes"]


def sample_profile(independent=False, source="cc4"):
    points = [CurvePoint(0, .5, .2), CurvePoint(10, 1.7, .9, True),
              CurvePoint(30, .8, .3), CurvePoint(60, 1.4, .7)]
    vertical = deepcopy(points)
    if independent:
        vertical[1].multiplier = 1.2
    return CurveProfile("Weighted", deepcopy(points), vertical, source_format=source,
                        cc4_document={"KeepMe": "unmodified"}, ccurve_tail=["old settings"])


def show_profile(win, profile):
    page = win.calibration_page
    page._profile = deepcopy(profile)
    page._set_profile_chart(profile)
    return page


def set_style(page, value):
    page.graph_style_combo.setCurrentIndex(page.graph_style_combo.findData(value))


def test_style_toggle_all_previews_and_persistence_without_profile_mutation(make_window):
    win = make_window()
    page = show_profile(win, sample_profile())
    before = deepcopy(page._profile)
    page.chart._view = (.1, .9, .2, .8)
    win.recommendation_page.show_curve_profile(before)
    win.chat_page._show_preview(before, before, "Preview")
    win.dynamic_curve.base = deepcopy(before)
    win.dynamic_curve.proposed = deepcopy(before)
    win.dynamic_curve.changed.emit()
    charts = [page.chart, win.recommendation_page.chart, win.chat_page.quick_preview,
              win.recommendation_page.dynamic_curve_panel.chart,
              win.training_page.dynamic_curve_panel.chart, win.training_page.dynamic_curve_monitor.chart]
    events = []
    page.profile_changed.connect(events.append)
    assert all(c._graph_style == "cc3" for c in charts)
    assert page.chart._interpolate_points
    set_style(page, "cc4")
    assert all(c._graph_style == "cc4" for c in charts)
    assert not page.chart._interpolate_points
    assert all("cc4" in c.accessibleDescription().lower() and "approx" in c.accessibleDescription().lower() for c in charts)
    assert page._profile == before and page.chart._view == (.1, .9, .2, .8)
    assert not page._undo and events == []
    assert page._sample_settings.value("calibration/graph_style") == "cc4"
    assert all(not c._editable for c in charts[1:])
    assert not page.point_weight.isHidden()
    set_style(page, "cc3")
    assert page.chart._interpolate_points and page.point_weight.isHidden()
    assert all(c._graph_style == "cc3" for c in charts)
    assert page._profile == before and not page._undo and not events
    win.close()


def test_cc4_curve_responds_to_weights_but_cc3_ignores_them(qapp):
    chart = CurveChart(interpolate_points=True)
    chart.resize(500, 300)
    chart.points = [.1, .9, .2, .8]
    chart.xs = [0, .2, .6, 1]
    rect = chart._plot_rect()
    low, high = [.5, .01, .5, .5], [.5, 1, .5, .5]
    assert chart._curve_path(rect, chart.points, chart.x_positions(), low) == chart._curve_path(rect, chart.points, chart.x_positions(), high)
    chart.set_graph_style("cc4")
    assert chart._curve_path(rect, chart.points, chart.x_positions(), low) != chart._curve_path(rect, chart.points, chart.x_positions(), high)
    assert "weighted" in chart.accessibleDescription().lower()
    chart.close()


def test_current_and_previous_paths_use_their_own_weights(qapp):
    chart = CurveChart(interpolate_points=True)
    chart.resize(500, 300)
    chart.points = [.2, .9, .3, .8]
    chart.xs = [0, .3, .7, 1]
    chart.set_previous(list(chart.points), chart.x_positions())
    low, high = [.5, .05, .5, .5], [.5, 1, .5, .5]
    chart.set_curve_weights(high, low)
    chart.set_graph_style("cc4")
    rect = chart._plot_rect()
    current = chart._curve_path(rect, chart.points, chart.x_positions(), chart.weights)
    previous = chart._curve_path(rect, chart.previous_points, chart.previous_xs, chart.previous_weights)
    assert current != previous
    chart.set_graph_style("cc3")
    current = chart._curve_path(rect, chart.points, chart.x_positions(), chart.weights)
    previous = chart._curve_path(rect, chart.previous_points, chart.previous_xs, chart.previous_weights)
    assert current == previous
    chart.close()


def test_weight_field_edits_linked_axes_and_roundtrips_undo_redo(make_window, tmp_path):
    win = make_window()
    p = sample_profile()
    page = show_profile(win, p)
    page.point_combo.setCurrentIndex(2)
    set_style(page, "cc4")
    rect = page.chart._plot_rect()
    old_path = page.chart._curve_path(rect, page.chart.points, page.chart.x_positions())
    page.point_weight.setText("0.123456789123456")
    page.point_apply_btn.click()
    updated = page._profile
    assert updated.horizontal[1].smoothness == float("0.123456789123456")
    assert updated.vertical == updated.horizontal
    assert updated.horizontal[1].corner is True
    assert updated.horizontal[1].speed == 10 and updated.horizontal[1].multiplier == 1.7
    assert page.chart._curve_path(rect, page.chart.points, page.chart.x_positions()) != old_path
    assert updated.cc4_document["KeepMe"] == "unmodified" and updated.ccurve_tail == ["old settings"]
    path = save_curve(updated, tmp_path / "test.cc4")
    loaded = load_curve(path)
    assert loaded.horizontal[1].smoothness == updated.horizontal[1].smoothness
    assert loaded.cc4_document["KeepMe"] == "unmodified"
    page.undo()
    assert page._profile == p and float(page.point_weight.text()) == .9
    page.redo()
    assert page._profile.horizontal[1].smoothness == float("0.123456789123456")
    set_style(page, "cc3")
    assert page._profile.horizontal[1].smoothness == float("0.123456789123456")
    win.close()


def test_independent_vertical_and_invalid_weight_never_mutate(make_window):
    win = make_window()
    p = sample_profile(independent=True)
    page = show_profile(win, p)
    set_style(page, "cc4")
    page.point_combo.setCurrentIndex(2)
    for invalid in ("-0.01", "1.1", "nan", "inf", "oops"):
        page.point_weight.setText(invalid)
        page.point_velocity.setText("12")  # all fields must commit atomically
        page._apply_numeric_point()
        assert page._profile == p and not page._undo
        assert not page.point_error.isHidden()
    page.point_velocity.setText("12")
    page.point_weight.setText("0")
    page._apply_numeric_point()
    assert page._profile.horizontal[1].speed == 12 and page._profile.horizontal[1].smoothness == 0
    assert page._profile.vertical == p.vertical and len(page._undo) == 1
    win.close()


def test_style_and_weight_controls_are_locked_during_calibration(make_window):
    win = make_window()
    page = show_profile(win, sample_profile())
    set_style(page, "cc4")
    page.point_combo.setCurrentIndex(2)
    before = deepcopy(page._profile)
    page.canvas._active = True
    page._set_curve_controls_enabled(False)
    assert not page.graph_style_combo.isEnabled() and not page.point_weight.isEnabled()
    set_style(page, "cc3")  # programmatic changes also cannot evade the lock
    assert page._graph_style == "cc4" and page.graph_style_combo.currentData() == "cc4"
    page.point_weight.setText(".2")
    page._apply_numeric_point()
    assert page._profile == before
    page.canvas._active = False
    page._set_curve_controls_enabled(True)
    assert page.graph_style_combo.isEnabled()
    win.close()


def test_cc3_export_warns_before_losing_weights_and_cc4_is_default(make_window, monkeypatch, tmp_path):
    win = make_window()
    p = sample_profile(source="generated")
    page = show_profile(win, p)
    set_style(page, "cc4")
    captured = []
    cc3_path = tmp_path / "no-weights.ccurve"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda _p, _t, default, filters: (captured.append((default, filters)) or str(cc3_path), ""))
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: (questions.append(args[2]) or QMessageBox.StandardButton.No))
    page._save()
    assert captured[0][0].endswith(".cc4") and captured[0][1].startswith("Custom Curve 4")
    assert "weights" in questions[0].lower() and not cc3_path.exists() and page._profile == p
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page._save()
    assert cc3_path.exists() and page._profile == p
    assert "cannot be represented" in page.hint.text().lower()
    cc4_path = tmp_path / "with-weights.cc4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(cc4_path), ""))
    page._save()
    assert load_curve(cc4_path).horizontal[1].smoothness == p.horizontal[1].smoothness
    win.close()


def test_cc3_to_cc4_warns_of_legacy_settings_loss(make_window, monkeypatch, tmp_path):
    win = make_window()
    p = sample_profile(source="ccurve")
    page = show_profile(win, p)
    set_style(page, "cc4")
    path = tmp_path / "converted.cc4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    text = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: (text.append(args[2]) or QMessageBox.StandardButton.No))
    page._save()
    assert not path.exists() and page._profile == p
    assert "CC3-only" in text[0]
    win.close()


def test_selected_point_in_large_weighted_curve_remains_accessible(make_window):
    win = make_window()
    points = [CurvePoint(i, 1 + i / 1000) for i in range(180)]
    page = show_profile(win, CurveProfile("long", points, deepcopy(points)))
    set_style(page, "cc4")
    page.point_combo.setCurrentIndex(138)
    assert page.point_combo.currentData() == 137
    assert 137 in page.chart.dot_indices() and len(page.chart.dot_indices()) == 180
    page.point_weight.setText(".75")
    page._apply_numeric_point()
    assert page._profile.horizontal[137].smoothness == .75
    assert page._profile.horizontal[136].smoothness == .5
    win.close()


def test_remembered_style_restored_across_window_recreation(make_window):
    win = make_window()
    set_style(win.calibration_page, "cc4")
    win.calibration_page._sample_settings.sync()
    win.close()
    second = make_window()
    assert second.calibration_page.graph_style_combo.currentData() == "cc4"
    assert second.calibration_page.chart._graph_style == "cc4"
    assert second.chat_page.quick_preview._graph_style == "cc4"
    assert second.training_page.dynamic_curve_monitor.chart._graph_style == "cc4"
    second.close()


def test_cc4_point_drag_preserves_point_weights_and_undo(make_window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = make_window()
    p = sample_profile()
    page = show_profile(win, p)
    set_style(page, "cc4")
    chart = page.chart
    rect = chart._plot_rect()
    origin = chart._pt(rect, chart.x_positions()[1], chart.points[1]).toPoint()
    target = chart._pt(rect, .3, .6).toPoint()
    QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(chart, target)
    QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=target)
    assert page._profile.horizontal[1].speed != p.horizontal[1].speed
    assert [a.smoothness for a in page._profile.horizontal] == [a.smoothness for a in p.horizontal]
    assert page.point_combo.currentData() == 1 and float(page.point_weight.text()) == .9
    page.undo()
    assert page._profile == p
    win.close()


def test_context_menu_selects_weight_field_only_in_cc4(make_window):
    win = make_window()
    page = show_profile(win, sample_profile())
    point = page.chart._pt(page.chart._plot_rect(), page.chart.x_positions()[1], page.chart.points[1]).toPoint()
    menu = page.chart.build_menu(point)
    assert not any("weight" in a.text().lower() for a in menu.actions())
    menu.deleteLater()
    set_style(page, "cc4")
    menu = page.chart.build_menu(point)
    action = next(a for a in menu.actions() if "weight" in a.text().lower())
    assert action.isEnabled()
    action.trigger()
    assert page.point_combo.currentData() == 1 and page.point_weight.isEnabled()
    assert page.point_weight.selectedText() == "0.9"
    menu.deleteLater()
    win.close()
