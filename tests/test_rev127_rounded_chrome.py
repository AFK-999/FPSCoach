"""Rounded window regions, right-aligned controls, and refreshed bot/chart icon."""
from __future__ import annotations

import pytest
from fps_coach.ui import icons
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import dynamic_curve, main_window

    def create(theme="dark"):
        settings = QSettings(str(tmp_path / (theme + ".ini")), QSettings.Format.IniFormat)
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
def test_right_aligned_buttons_keep_actions_accessibility_and_theme(make_window, theme):
    w = make_window(theme)
    title = w.title_bar
    a, b, c = title.minimize_button, title.fullscreen_button, title.close_button
    assert 0 < title.title.x() < title.width() / 2
    assert title.width() / 2 < a.x() < b.x() < c.x()
    assert title.width() - (c.x() + c.width()) <= 20
    assert [item.accessibleName() for item in (a, b, c)] == [
        "Minimize window", "Enter full screen", "Close window",
    ]
    assert all(item.isVisible() and item.width() == 26 for item in (a, b, c))
    b.setFocus()
    QTest.keyClick(b, Qt.Key.Key_Space)
    QTest.qWait(20)
    assert w.isFullScreen() and b.accessibleName() == "Exit full screen"
    b.click()
    QTest.qWait(20)
    assert not w.isFullScreen()
    w.close()


def check_rounded_region(w):
    mask = w.mask()
    assert not mask.isEmpty()
    assert not mask.contains(QPoint(0, 0))
    assert not mask.contains(QPoint(w.width() - 1, w.height() - 1))
    assert mask.contains(QPoint(w.width() // 2, 0))
    assert mask.contains(QPoint(5, 5))  # retained corner resize target
    assert mask.contains(QPoint(w.width() // 2, w.height() // 2))


def test_rounding_updates_on_resize_maximize_fullscreen_and_restore(make_window):
    w = make_window()
    check_rounded_region(w)
    w.resize(1550, 920)
    QTest.qWait(20)
    check_rounded_region(w)
    assert w.centralWidget()._edges_at(QPoint(5, 5)) == Qt.Edge.TopEdge | Qt.Edge.LeftEdge
    w.showMaximized()
    QTest.qWait(20)
    check_rounded_region(w)  # maximized is not full screen
    w.title_bar.fullscreen_button.click()
    QTest.qWait(20)
    assert w.isFullScreen() and w.mask().isEmpty()
    w.title_bar.fullscreen_button.click()
    QTest.qWait(20)
    assert w.isMaximized()
    check_rounded_region(w)
    w.showNormal()
    QTest.qWait(20)
    check_rounded_region(w)
    w.close()


def test_fresh_bot_chart_logo_assets_are_available(qapp):
    from pathlib import Path
    from xml.etree import ElementTree

    root = Path(__file__).resolve().parents[1] / "python/fps_coach/assets/branding"
    text = (root / "curvecoach-logo.svg").read_text()
    svg = ElementTree.fromstring(text)
    assert svg.attrib["viewBox"] == "0 0 24 24"
    assert "robot" in text.lower() and "curve chart" in text.lower()
    assert "linearGradient" in text and "#d7f249" in text
    assert not QIcon(str(root / "curvecoach.ico")).isNull()
    image = icons.pixmap("brand", "#d7f249", 64).toImage()
    assert not image.isNull()
    assert sum(image.pixelColor(x, y).alpha() > 0 for x in range(64) for y in range(64)) > 2000
