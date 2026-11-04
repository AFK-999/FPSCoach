"""Rev121 supersedes the Calibration dynamic-view selector from rev116."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fps_coach.backend import _FallbackAnalyzer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("size", [(900, 700), (1200, 800), (1600, 1000)])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_calibration_contains_only_sensitivity_graph(qapp, size, theme):
    from fps_coach.ui.pages_setup import CalibrationPage
    from fps_coach.ui.styles import build_style
    old = qapp.styleSheet()
    qapp.setStyleSheet(build_style("balanced", theme))
    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP.append(page)
    page.resize(*size)
    page.show()
    QTest.qWait(50)
    assert page.curve_title.text() == "Sensitivity curve"
    assert not hasattr(page, "dynamic_chart")
    assert not hasattr(page, "curve_settings_btn")
    assert not hasattr(page, "dynamic_curve_panel")
    assert not any("dynamic performance curve" in label.text().lower() for label in page.findChildren(QLabel))
    assert page.chart.isVisible() and page.chart._editable
    assert page.import_btn.isEnabled() and page.save_btn.isEnabled()
    page.close()
    qapp.setStyleSheet(old)


def test_sensitivity_edit_controls_still_lock_during_calibration(qapp):
    from fps_coach.ui.pages_setup import CalibrationPage
    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP.append(page)
    page._checkpoint()
    page.canvas._active = True
    page._set_curve_controls_enabled(False)
    assert not page.undo_btn.isEnabled() and not page.chart._editable
    original = len(page._undo)
    page.undo()
    assert len(page._undo) == original
    page.canvas._active = False
    page._set_curve_controls_enabled(True)
    assert page.undo_btn.isEnabled() and page.chart._editable
    page.close()
