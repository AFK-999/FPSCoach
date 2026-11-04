"""Theme-aware custom window chrome, accessible controls and CurveCoach branding."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent, QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import dynamic_curve, main_window

    def create(theme="dark"):
        s = QSettings(str(tmp_path / (theme + ".ini")), QSettings.Format.IniFormat)
        s.setValue("ui/theme", theme)
        monkeypatch.setattr(main_window, "_settings", lambda: s)
        monkeypatch.setattr(dynamic_curve, "_settings", lambda: s)
        monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
        w = main_window.MainWindow()
        _KEEP.append(w)
        w.resize(1400, 900)
        w.show()
        QTest.qWait(20)
        return w
    return create


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_title_theme_brand_and_accessible_controls(make_window, theme):
    from fps_coach.ui.styles import PALETTES

    win = make_window(theme)
    assert win.windowTitle() == "CurveCoach" and not win.windowIcon().isNull()
    assert win.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert "CURVECOACH" in [label.text() for label in win.findChildren(QLabel)]
    assert PALETTES[theme]["surface2"] in win.styleSheet()
    bar = win.title_bar
    assert bar.title.text() == "CurveCoach"
    controls = (bar.close_button, bar.minimize_button, bar.fullscreen_button)
    assert [b.accessibleName() for b in controls] == ["Close window", "Minimize window", "Enter full screen"]
    assert all(b.width() == b.height() == 26 and b.isVisible() for b in controls)
    assert all(b.focusPolicy() != Qt.FocusPolicy.NoFocus for b in controls)
    assert win._brand.property("glyph") == "brand"
    assert all(button.isVisible() for button in win._nav_buttons.values())
    win.close()


def test_full_screen_and_restore_from_normal_and_maximized(make_window):
    w = make_window()
    green = w.title_bar.fullscreen_button
    green.click()
    QTest.qWait(20)
    assert w.isFullScreen() and green.accessibleName() == "Exit full screen"
    assert w._shell_layout.contentsMargins().left() == 0
    green.click()
    QTest.qWait(20)
    assert not w.isFullScreen() and not w.isMaximized()
    assert w._shell_layout.contentsMargins().left() == 6
    w.showMaximized()
    QTest.qWait(20)
    assert w.isMaximized()
    green.click()
    QTest.qWait(20)
    assert w.isFullScreen()
    green.click()
    QTest.qWait(20)
    assert w.isMaximized() and not w.isFullScreen()
    w.close()


def test_minimize_and_close_use_real_window_lifecycle(make_window, monkeypatch):
    w = make_window()
    w.title_bar.minimize_button.click()
    QTest.qWait(10)
    assert w.isMinimized()
    w.showNormal()
    QTest.qWait(10)
    closed = []
    monkeypatch.setattr(w._backend, "close", lambda: closed.append(True))
    w.title_bar.close_button.setFocus()
    QTest.keyClick(w.title_bar.close_button, Qt.Key.Key_Space)
    assert not w.isVisible() and closed == [True]


def test_theme_system_changes_only_when_system_selected(make_window, monkeypatch):
    w = make_window()
    changes = []
    monkeypatch.setattr(w, "_apply_theme", lambda: changes.append("applied"))
    w.s.setValue("ui/theme", "dark")
    w._system_theme_changed(Qt.ColorScheme.Light)
    assert not changes
    w.s.setValue("ui/theme", "system")
    w._system_theme_changed(Qt.ColorScheme.Light)
    assert changes == ["applied"]
    w.close()


def test_resize_edges_and_pointer_fallback_obey_minimum(make_window, monkeypatch):
    from fps_coach.ui.window_chrome import ResizeShell

    w = make_window()
    shell = w.centralWidget()
    assert isinstance(shell, ResizeShell)
    assert shell._edges_at(QPoint(1, 1)) == Qt.Edge.LeftEdge | Qt.Edge.TopEdge
    assert shell._edges_at(QPoint(shell.width() - 1, shell.height() - 1)) == Qt.Edge.RightEdge | Qt.Edge.BottomEdge
    assert shell._edges_at(QPoint(50, 50)) == Qt.Edge(0)
    monkeypatch.setattr(QWindow, "startSystemResize", lambda _w, _edges: False)
    start = QPointF(shell.width() - 2, shell.height() - 2)
    global_start = QPointF(shell.mapToGlobal(start.toPoint()))
    old = w.size()
    shell.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, start, global_start,
                                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                      Qt.KeyboardModifier.NoModifier))
    delta = QPointF(50, 30)
    shell.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, start + delta, global_start + delta,
                                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                     Qt.KeyboardModifier.NoModifier))
    assert w.width() == old.width() + 50 and w.height() == old.height() + 30
    shell.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, start + delta, global_start + delta,
                                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                        Qt.KeyboardModifier.NoModifier))
    w.showFullScreen()
    QTest.qWait(20)
    assert shell._edges_at(QPoint(1, 1)) == Qt.Edge(0)
    w.close()


def test_logo_svg_and_windows_icon_assets():
    root = Path(__file__).resolve().parents[1] / "python/fps_coach/assets/branding"
    svg = ElementTree.parse(root / "curvecoach-logo.svg").getroot()
    assert svg.attrib["viewBox"] == "0 0 24 24"
    text = (root / "curvecoach-logo.svg").read_text()
    assert "robot" in text.lower() and "curve chart" in text.lower()
    assert (root / "curvecoach.ico").stat().st_size > 1000


def test_fullscreen_keyboard_shortcut_and_window_menu(make_window, monkeypatch):
    from PySide6.QtWidgets import QMenu

    w = make_window()
    key = next(s for s in w._window_shortcuts if s.key().toString() == "F11")
    key.activated.emit()
    QTest.qWait(20)
    assert w.isFullScreen()
    key.activated.emit()
    QTest.qWait(20)
    assert not w.isFullScreen()
    opened = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _pos: opened.append([a.text() for a in menu.actions()]))
    w.title_bar.open_window_menu()
    assert opened == [["Restore", "Maximize", "Minimize", "Enter full screen", "", "Close"]]
    w.close()


def test_title_drag_fallback_moves_window(make_window, monkeypatch):
    w = make_window()
    monkeypatch.setattr(QWindow, "startSystemMove", lambda _window: False)
    bar = w.title_bar
    position = QPointF(200, 15)
    global_position = QPointF(bar.mapToGlobal(position.toPoint()))
    original = w.pos()
    bar.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, position, global_position,
                                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                    Qt.KeyboardModifier.NoModifier))
    delta = QPointF(30, 25)
    bar.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, position + delta, global_position + delta,
                                   Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.NoModifier))
    assert w.pos() == original + delta.toPoint()
    bar.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, position + delta, global_position + delta,
                                      Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                      Qt.KeyboardModifier.NoModifier))
    w.close()


def test_fullscreen_change_releases_in_progress_raw_capture(make_window, monkeypatch):
    from fps_coach.ui import raw_mouse_capture
    from test_raw_calibration import FakeAPI

    api = FakeAPI()
    original = raw_mouse_capture.RawMouseCapture
    monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture",
                        lambda arena, move, click, lost, **kw: original(
                            arena, move, click, lost, api=api, **kw))
    w = make_window()
    w._select_page("calibration")
    canvas = w.calibration_page.canvas
    canvas.set_input_mode("raw")
    QTest.qWait(50)  # let input-path layout settle before capture
    canvas.start_session()
    assert canvas._active and canvas._raw_capture is not None and api.rect is not None
    w.title_bar.fullscreen_button.click()
    QTest.qWait(30)
    assert canvas._paused_for_navigation and canvas._raw_capture is None and api.rect is None
    canvas.cancel_session()
    w.close()
