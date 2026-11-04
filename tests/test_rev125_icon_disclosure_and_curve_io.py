"""Icon-only Coach disclosure and corrected Calibration import/export glyphs."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fps_coach.curve_io import CurvePoint, CurveProfile, load_curve, save_curve
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [(900, 700), (1350, 900)])
def test_disclosure_is_icon_only_and_title_moves_inside_expansion(qapp, monkeypatch, tmp_path, theme, size):
    from fps_coach.ui import dynamic_curve, main_window

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/theme", theme)
    monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
    monkeypatch.setattr(main_window, "_settings", lambda: settings)
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    win = main_window.MainWindow()
    _KEEP.append(win)
    win.resize(*size)
    win.show()
    assert not hasattr(win.chat_page, "dynamic_curve_section")
    win._select_page("recommendation")
    rec = win.recommendation_page
    rec.curve_deck._animations_enabled = lambda: False
    title = next(label for label in rec.dynamic_curve_panel.findChildren(QLabel)
                 if label.text() == "Dynamic Performance Curve")
    assert rec.dynamic_curve_btn.accessibleName() == "Dynamic Performance Curve"
    assert not title.isVisible() and not win.dynamic_curve.enabled
    rec.dynamic_curve_btn.setFocus()
    QTest.keyClick(rec.dynamic_curve_btn, Qt.Key.Key_Space)
    assert title.isVisible() and not win.dynamic_curve.enabled
    assert rec.dynamic_curve_panel.chart._plot_rect().width() == rec.dynamic_curve_panel.chart._plot_rect().height()
    rec.dynamic_curve_panel.enabled_box.setChecked(True)
    assert win.calibration_page.canvas._performance_enabled
    assert win.training_page.dynamic_curve_panel.enabled_box.isChecked()
    rec.dynamic_back_btn.click()
    assert not title.isVisible() and win.dynamic_curve.enabled
    win.dynamic_curve.set_enabled(False)
    win.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_swapped_calibration_glyphs_keep_file_actions(qapp, monkeypatch, tmp_path, theme):
    from fps_coach.ui.pages_setup import CalibrationPage
    from fps_coach.ui.styles import build_style, set_active_theme
    from fps_coach.ui.widgets import refresh_glyphs

    qapp.setStyleSheet(build_style("luxurious", theme))
    set_active_theme(theme)
    page = CalibrationPage(SimpleNamespace(analyzer=None))
    _KEEP.append(page)
    page.show()
    refresh_glyphs(page)
    assert page.import_btn.text().strip() == "Import"
    assert page.save_btn.text().strip() == "Export"
    assert page.import_btn.property("glyph") == "download"
    assert page.save_btn.property("glyph") == "upload"
    assert not page.import_btn.icon().isNull() and not page.save_btn.icon().isNull()
    points = [CurvePoint(0, 1), CurvePoint(100, 1.1)]
    profile = CurveProfile("Input test", points, deepcopy(points))
    original = tmp_path / "source.cc4"
    destination = tmp_path / "output.cc4"
    save_curve(profile, original)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *args, **kwargs: (str(original), "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *args, **kwargs: (str(destination), "")))
    page.import_btn.click()
    assert page._profile == load_curve(original) and "Imported" in page.hint.text()
    page.save_btn.click()
    assert destination.exists() and load_curve(destination) == page._profile
    assert "Exported" in page.hint.text()
    page.close()
    qapp.setStyleSheet("")


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("analysis_enabled", [False, True])
def test_icon_button_has_no_text_or_other_marks_outside_bot(qapp, theme, analysis_enabled):
    from fps_coach.ui.dynamic_curve import PerformanceDisclosure
    from fps_coach.ui.guide_book import COACH_BUTTON_SIZE, paint_guide_button_surface
    from fps_coach.ui.styles import build_style
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QPushButton

    class SurfaceOnly(QPushButton):
        def __init__(self):
            super().__init__("")
            self.setObjectName("performanceDisclosure")
            self.setFixedSize(*COACH_BUTTON_SIZE)

        def paintEvent(self, event):  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_guide_button_surface(painter, self)
            painter.end()

    qapp.setStyleSheet(build_style("luxurious", theme))
    surface, button = SurfaceOnly(), PerformanceDisclosure()
    _KEEP.extend([surface, button])
    button.set_analysis_enabled(analysis_enabled)
    for widget in (surface, button):
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widget.show()
        widget.clearFocus()
    qapp.processEvents()
    assert surface.underMouse() == button.underMouse()
    assert surface.hasFocus() == button.hasFocus()
    a, b = surface.grab().toImage(), button.grab().toImage()
    changed = [(x, y) for y in range(b.height()) for x in range(b.width())
               if a.pixel(x, y) != b.pixel(x, y)]
    assert changed  # robot is visible
    assert all(56 <= x <= 94 and 7 <= y <= 40 for x, y in changed)
    surface.close()
    button.close()
    qapp.setStyleSheet("")
