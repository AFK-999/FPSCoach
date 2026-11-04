"""Recommendation action placement and compact accessible bot control."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import dynamic_curve, main_window, pages_setup

    def create(theme="dark"):
        settings = QSettings(str(tmp_path / f"{theme}.ini"), QSettings.Format.IniFormat)
        settings.setValue("ui/theme", theme)
        settings.setValue("ui/animations", False)
        for module in (dynamic_curve, main_window, pages_setup):
            monkeypatch.setattr(module, "_settings", lambda: settings)
        monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
        win = main_window.MainWindow()
        _KEEP.append(win)
        win.resize(1400, 900)
        win.show()
        win._select_page("recommendation")
        QTest.qWait(30)
        return win
    return create


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [(1400, 900), (1600, 1000), (1241, 700)])
def test_generate_stays_at_bottom_of_summary_for_recommendation(make_window, theme, size):
    w = make_window(theme)
    w.resize(*size)
    page = w.recommendation_page
    page.update_result({"trial_count": 12}, {
        "title": "Add control through the middle of the curve",
        "sensitivity_multiplier": 1.1, "confidence": 0.8,
        "reasons": ["Measured change", "Use your results"], "points": [(0, 1), (1, 1.1)],
    })
    QTest.qWait(30)
    button, bot = page.generate_btn, page.dynamic_curve_btn
    card = button.parentWidget()
    assert page.title_label.text() == "Add control through the middle of the curve"
    assert button.parentWidget() is bot.parentWidget()
    assert 0 <= card.height() - (button.y() + button.height()) <= 24
    assert bot.y() < button.y()
    assert button.isEnabled()
    page.set_calibration_in_progress(True)
    assert not button.isEnabled()
    page.set_calibration_in_progress(False)
    assert button.isEnabled()
    w.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_small_square_bot_button_is_accessible_and_flips_without_enabling(make_window, theme):
    w = make_window(theme)
    page = w.recommendation_page
    button = page.dynamic_curve_btn
    assert button.size().width() == button.size().height() == 42
    assert button.text() == ""
    assert button.accessibleName() == "Dynamic Performance Curve"
    assert "Dynamic Performance Curve" in button.toolTip()
    assert "Suggested curve is shown" in button.accessibleDescription()
    assert not hasattr(w.chat_page, "dynamic_curve_section")
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert page.curve_deck.currentIndex() == 1
    assert "Dynamic curve is shown" in button.accessibleDescription()
    assert not w.dynamic_curve.enabled and w.calibration_page._profile is None
    assert page.dynamic_curve_panel.chart.width() == page.dynamic_curve_panel.chart.height()
    page.dynamic_back_btn.click()
    assert page.curve_deck.currentIndex() == 0 and not w.dynamic_curve.enabled
    w.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_square_button_draws_only_a_bot_on_start_surface(qapp, theme):
    from fps_coach.ui.guide_book import PerformanceStartButton, paint_guide_button_surface
    from fps_coach.ui.styles import build_style

    class SurfaceOnly(QPushButton):
        def __init__(self):
            super().__init__("")
            self.setObjectName("performanceStart")
            self.setFixedSize(42, 42)

        def paintEvent(self, event):  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_guide_button_surface(painter, self)
            painter.end()

    qapp.setStyleSheet(build_style("luxurious", theme))
    surface, bot = SurfaceOnly(), PerformanceStartButton()
    _KEEP.extend([surface, bot])
    for widget in (surface, bot):
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widget.show()
        widget.clearFocus()
    qapp.processEvents()
    assert surface.underMouse() == bot.underMouse()
    plain, painted = surface.grab().toImage(), bot.grab().toImage()
    diff = [(x, y) for y in range(42) for x in range(42)
            if plain.pixel(x, y) != painted.pixel(x, y)]
    assert diff and all(10 <= x <= 32 and 6 <= y <= 32 for x, y in diff)
    surface.close()
    bot.close()
    qapp.setStyleSheet("")
