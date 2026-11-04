"""Navigation chrome rounds with the window without changing navigation behavior."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import dynamic_curve, main_window

    def create(theme="dark"):
        settings = QSettings(str(tmp_path / f"{theme}.ini"), QSettings.Format.IniFormat)
        settings.setValue("ui/theme", theme)
        monkeypatch.setattr(main_window, "_settings", lambda: settings)
        monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
        monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
        win = main_window.MainWindow()
        _KEEP.append(win)
        win.resize(1400, 900)
        win.show()
        QTest.qWait(20)
        return win
    return create


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_navigation_has_rounded_theme_aware_frame(make_window, theme):
    from fps_coach.ui.styles import PALETTES

    w = make_window(theme)
    bar = w.findChild(QFrame, "topbar")
    assert bar and bar.height() == 60
    rule = w.styleSheet().split("QFrame#topbar", 1)[1].split("}", 1)[0]
    assert "border-radius: 8px" in rule and "border: 1px solid" in rule
    im = w.grab().toImage()
    bg = QColor(PALETTES[theme]["bg"])
    for x, y in [(0, 0), (bar.width()-1, 0), (0, bar.height()-1), (bar.width()-1, bar.height()-1)]:
        assert im.pixelColor(bar.x() + x, bar.y() + y) == bg
    assert im.pixelColor(bar.x() + 8, bar.y() + bar.height() // 2) == QColor(PALETTES[theme]["surface"])
    assert bar.x() == w.title_bar.x() and bar.width() == w.title_bar.width()
    assert bar.y() - (w.title_bar.y() + w.title_bar.height()) == 3
    w.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_tabs_and_settings_remain_visible_and_navigate_at_minimum_width(make_window, theme):
    w = make_window(theme)
    w.resize(w.minimumWidth(), 700)
    QTest.qWait(30)
    bar = w.findChild(QFrame, "topbar")
    scroll = w.findChild(QScrollArea, "navScroll")
    assert scroll.height() == 54 and scroll.horizontalScrollBar().maximum() == 0
    for key in ("dashboard", "calibration", "training", "chat"):
        button = w._nav_buttons[key]
        assert button.isVisible() and button.height() == 54
        assert 0 <= button.mapTo(bar, button.rect().topLeft()).y()
        button.click()
        assert w.stack.currentWidget() is w._pages[key] and button.isChecked()
    settings = w._util_buttons["settings"]
    assert settings.isVisible() and settings.accessibleName() == "Settings"
    settings.click()
    assert w.stack.currentWidget() is w.settings_page and settings.isChecked()
    w.close()


def test_resize_fullscreen_and_coach_strip_preserve_rounded_bar(make_window):
    w = make_window()
    bar = w.findChild(QFrame, "topbar")
    w.coach_panel.show()
    QTest.qWait(20)
    assert w.coach_panel.y() - (bar.y() + bar.height()) == 3
    w.showFullScreen()
    QTest.qWait(20)
    assert w.isFullScreen() and w.mask().isEmpty() and bar.isVisible()
    w._nav_buttons["chat"].click()
    assert w.stack.currentWidget() is w.chat_page
    w.showNormal()
    w.resize(1500, 900)
    QTest.qWait(20)
    assert not w.mask().isEmpty() and bar.width() == w.title_bar.width()
    assert w._util_buttons["settings"].isVisible()
    w.close()
