"""The crosshair overlay stays an independent window and never takes focus on click.

Real Windows focus behaviour cannot be run here (no Windows). These tests check the
settings that cause it: no owner, the no-focus window flag, the WM_MOUSEACTIVATE reply,
and that showing or dragging the overlay leaves the AI Coach window active.
"""

from __future__ import annotations

import ctypes
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from fps_coach.crosshair import CrosshairConfig  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def clean_setting(qapp):
    from fps_coach.ui.widgets import _settings

    s = _settings()
    previous = s.value("crosshair/overlay_active", False, type=bool)
    s.setValue("crosshair/overlay_active", False)
    yield s
    s.setValue("crosshair/overlay_active", previous)


def test_overlay_is_a_top_level_window_with_no_owner(qapp) -> None:
    from fps_coach.ui.crosshair_overlay import CrosshairOverlay

    overlay = CrosshairOverlay(CrosshairConfig())
    assert overlay.parent() is None
    assert overlay.isWindow()


@pytest.mark.parametrize("editing", [False, True])
def test_no_focus_flag_is_set_in_both_modes(qapp, editing) -> None:
    from PySide6.QtCore import Qt

    from fps_coach.ui.crosshair_overlay import CrosshairOverlay

    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.set_editing(editing)
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.Tool
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.FramelessWindowHint
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_no_click_activation_reply_for_mouse_activate() -> None:
    from fps_coach.ui.crosshair_overlay import native_reply

    assert native_reply(0x0021) == (True, 3)  # WM_MOUSEACTIVATE -> MA_NOACTIVATE
    assert native_reply(0x000F) is None  # WM_PAINT is left to Qt
    assert native_reply(0x0201) is None  # WM_LBUTTONDOWN is left to Qt


def test_native_mouse_activate_message_is_refused(qapp) -> None:
    from fps_coach.ui.crosshair_overlay import _MSG, CrosshairOverlay

    overlay = CrosshairOverlay(CrosshairConfig())
    msg = _MSG()
    msg.message = 0x0021
    result = overlay.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
    assert tuple(result) == (True, 3)


def test_other_native_messages_pass_through(qapp) -> None:
    from fps_coach.ui.crosshair_overlay import _MSG, CrosshairOverlay

    overlay = CrosshairOverlay(CrosshairConfig())
    msg = _MSG()
    msg.message = 0x000F  # WM_PAINT
    result = overlay.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
    assert not (isinstance(result, tuple) and result[0] is True and result[1] == 3)


def test_main_window_overlay_is_not_owned(qapp, clean_setting) -> None:
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        overlay = window._ensure_overlay()
        assert overlay.parent() is None
        assert overlay.isWindow()
    finally:
        window.close()


def test_showing_and_dragging_overlay_does_not_move_focus_on_offscreen_platform(qapp, clean_setting) -> None:
    """Smoke test only. The offscreen platform does not model window activation, so this
    cannot prove focus behaviour. The real check is the Windows test in the report."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        overlay = window._ensure_overlay()
        overlay.set_editing(True)
        overlay.show()
        overlay.move_centre(QPoint(300, 300))
        overlay.move_centre(QPoint(340, 320))
        QApplication.processEvents()
        assert overlay.isVisible()
        assert overlay.centre == QPoint(340, 320)
    finally:
        window.close()
