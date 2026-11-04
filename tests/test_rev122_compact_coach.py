"""Matching compact Coach controls preserve disclosure and preset behavior."""
from __future__ import annotations

import pytest
from fps_coach.ui.guide_book import COACH_BUTTON_SIZE
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [(900, 700), (1350, 900), (1600, 1000)])
def test_equal_compact_controls_and_disclosure(qapp, monkeypatch, tmp_path, theme, size):
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
    win._select_page("chat")
    QTest.qWait(40)
    page = win.chat_page
    assert not hasattr(page, "dynamic_curve_section")
    buttons = (page.fast_flick_btn, page.precision_btn)
    assert COACH_BUTTON_SIZE == (150, 58)
    assert all((b.width(), b.height()) == COACH_BUTTON_SIZE for b in buttons)
    assert all(b.isVisible() and b.accessibleName() for b in buttons)
    assert not win.dynamic_curve.enabled
    win._select_page("recommendation")
    rec = win.recommendation_page
    rec.curve_deck._animations_enabled = lambda: False
    rec.dynamic_curve_btn.click()
    assert rec.curve_deck.currentIndex() == 1 and not win.dynamic_curve.enabled
    rec.dynamic_curve_panel.enabled_box.setChecked(True)
    assert win.training_page.dynamic_curve_panel.enabled_box.isChecked()
    rec.dynamic_back_btn.click()
    assert rec.curve_deck.currentIndex() == 0 and win.dynamic_curve.enabled
    win.dynamic_curve.set_enabled(False)
    win.close()
