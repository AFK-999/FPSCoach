"""Calibration simplification, square dynamic graphs and manual preset tiles."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fps_coach.curve_adjust import apply_preset
from fps_coach.curve_io import CurvePoint, CurveProfile
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def base():
    return CurveProfile("Test curve", [CurvePoint(0, 1), CurvePoint(50, 1.1), CurvePoint(100, 1.2)],
                        [CurvePoint(0, 1), CurvePoint(50, 1.1), CurvePoint(100, 1.2)])


def window_for_test(monkeypatch, tmp_path, theme):
    from fps_coach.ui import dynamic_curve, main_window
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/theme", theme)
    monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
    monkeypatch.setattr(main_window, "_settings", lambda: settings)
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    window = main_window.MainWindow()
    _KEEP.append(window)
    return window


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [(900, 700), (1350, 900), (1600, 1000)])
def test_coach_toggle_shared_state_square_charts_and_no_calibration_ui(qapp, monkeypatch, tmp_path, theme, size):
    win = window_for_test(monkeypatch, tmp_path, theme)
    win.resize(*size)
    win.show()
    win._select_page("calibration")
    QTest.qWait(50)
    page = win.calibration_page
    assert page.curve_title.text() == "Sensitivity curve"
    assert page.chart.isVisible()
    assert not hasattr(page, "dynamic_chart") and not hasattr(page, "curve_settings_btn")
    assert not hasattr(page, "dynamic_curve_panel")
    assert not any("dynamic performance curve" in x.text().lower() for x in page.findChildren(QLabel))
    assert not hasattr(win.chat_page, "dynamic_curve_section")
    win._select_page("recommendation")
    rec = win.recommendation_page
    rec.curve_deck._animations_enabled = lambda: False
    assert rec.curve_deck.currentIndex() == 0
    rec.dynamic_curve_btn.setFocus()
    QTest.keyClick(rec.dynamic_curve_btn, Qt.Key.Key_Space)
    assert rec.curve_deck.currentIndex() == 1
    panel = rec.dynamic_curve_panel
    assert panel.enabled_box.text() == "Enable AI Local Analysis"
    for chart in (panel.chart, win.training_page.dynamic_curve_panel.chart,
                  win.training_page.dynamic_curve_monitor.chart):
        assert chart.width() == chart.height()
        assert chart._plot_rect().width() == chart._plot_rect().height()
        assert not chart._editable
    panel.enabled_box.setChecked(True)
    assert win.calibration_page.canvas._performance_enabled
    assert win.training_page.dynamic_curve_panel.enabled_box.isChecked()
    panel.accuracy.setValue(83)
    assert win.training_page.dynamic_curve_panel.accuracy.value() == 83
    rec.dynamic_back_btn.click()
    assert rec.curve_deck.currentIndex() == 0 and win.dynamic_curve.enabled
    win.dynamic_curve.set_enabled(False)
    win.close()


def test_three_square_charts_show_same_proposal_without_modifying_calibration(qapp, monkeypatch, tmp_path, base):
    win = window_for_test(monkeypatch, tmp_path, "dark")
    _KEEP.append(win)
    win.show()
    engine = win.dynamic_curve
    engine.set_enabled(True)
    original = deepcopy(base)
    win.calibration_page._profile = deepcopy(base)
    proposal = deepcopy(base)
    proposal.horizontal[1].multiplier = 1.3
    engine.base, engine.proposed, engine._fresh = base, proposal, True
    engine.status = "Ready to confirm"
    engine.changed.emit()
    charts = (win.recommendation_page.dynamic_curve_panel.chart, win.training_page.dynamic_curve_panel.chart,
              win.training_page.dynamic_curve_monitor.chart)
    assert all(c.points == charts[0].points and c.previous_points == charts[0].previous_points for c in charts)
    assert win.calibration_page._profile == original
    engine.set_enabled(False)
    assert all(c.points == [] and c.previous_points is None for c in charts)
    win.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_preset_buttons_are_distinct_accessible_and_preview_only(qapp, monkeypatch, tmp_path, theme, base):
    win = window_for_test(monkeypatch, tmp_path, theme)
    win.show()
    win._select_page("chat")
    page = win.chat_page
    fast, precision = page.fast_flick_btn, page.precision_btn
    assert fast.key == "fast_flicks" and precision.key == "precision"
    assert "Fast Flick" in fast.accessibleName() and "Micro Aim" in precision.accessibleName()
    assert fast.size().width() == 150 and fast.size().height() == 58
    assert not fast.isEnabled() and not precision.isEnabled()
    assert "first" in fast.toolTip().lower()
    original = deepcopy(base)
    page.set_base_profile(base)
    assert fast.isEnabled() and precision.isEnabled()
    assert fast.grab().toImage() != precision.grab().toImage()
    sent = []
    page.curve_adjusted.connect(lambda *args: sent.append(args))
    fast.setFocus()
    QTest.keyClick(fast, Qt.Key.Key_Space)
    expected, _ = apply_preset(original, "fast_flicks")
    assert page._adjusted_profile == expected
    assert base == original and not sent and win.calibration_page._profile is None
    assert not page.apply_to_rec.isHidden() and page.quick_preview.previous_points
    page.set_base_profile(base)
    precision.click()
    expected, _ = apply_preset(original, "precision")
    assert page._adjusted_profile == expected and not sent
    assert win.calibration_page._profile is None
    page.apply_to_rec.click()
    assert len(sent) == 1 and win.calibration_page._profile == expected
    assert win.stack.currentWidget() is win.calibration_page
    win.close()


def test_preset_controls_follow_current_curve_and_do_not_duplicate_suggestion_chips(qapp, monkeypatch, tmp_path, base):
    win = window_for_test(monkeypatch, tmp_path, "dark")
    page = win.chat_page
    page.set_base_profile(base)
    page.input.setText("fast flick precision tracking")
    chips = [page.suggestion_bar.itemAt(i).widget() for i in range(page.suggestion_bar.count())]
    texts = [b.text() for b in chips if b is not None]
    assert not any("Manual preset · Optimize for fast flicks" in t for t in texts)
    assert not any("Manual preset · Precision micro-aim" in t for t in texts)
    assert any("tracking" in t.lower() for t in texts)
    assert any("precision" in t.lower() for t in texts)
    assert len(texts) <= 4
    page.set_base_profile(None)
    assert not page.fast_flick_btn.isEnabled() and not page.precision_btn.isEnabled()
    win.close()
