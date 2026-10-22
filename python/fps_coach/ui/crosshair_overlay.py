"""On-screen crosshair overlay: a transparent window drawn on the primary display.

Off by default. Two states:

* Locked (the normal state): the window is click-through (it ignores the mouse, so
  clicks reach whatever is underneath) and cannot be moved.
* Edit: the window accepts the mouse so the user can drag the crosshair to the spot
  they want. A dashed frame shows the area. Lock again to fix the position.

The overlay only draws. It does not read the screen, the game, or any process, and it
never injects into or hooks any process. It does not take keyboard focus.

Focus: the overlay is a top-level window with no owner. An owned window (a child of the
main window) can pull activation back to its owner on Windows, so it must not have one.
In both states it also refuses to be activated by a click (WM_MOUSEACTIVATE returns
MA_NOACTIVATE). Dragging it therefore keeps focus where it was, and the AI Coach window
does not take it back.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from fps_coach.crosshair import CrosshairConfig

from .widgets import _draw_crosshair

_MIN_SIDE = 32
_EDIT_MIN_SIDE = 200  # larger target area while editing, so it is easy to grab

_WM_MOUSEACTIVATE = 0x0021
_MA_NOACTIVATE = 3  # receive the click, but do not activate this window
_WINDOWS_MSG_EVENT = b"windows_generic_MSG"


class _MSG(ctypes.Structure):
    """Win32 MSG layout. Only `message` is read; the rest keeps the layout correct."""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD),
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


def native_reply(message_id: int) -> tuple[bool, int] | None:
    """Return the (handled, result) reply for a Win32 message, or None to pass it on.

    Clicking the overlay must not activate it, so WM_MOUSEACTIVATE is answered with
    MA_NOACTIVATE. The mouse events still arrive, so dragging works.
    """
    if message_id == _WM_MOUSEACTIVATE:
        return True, _MA_NOACTIVATE
    return None


def overlay_side(cfg: CrosshairConfig) -> int:
    """Edge length of the square window that holds the crosshair with its outline."""
    reach = cfg.size + cfg.gap + max(cfg.thickness, cfg.dot_size) + 6
    return max(_MIN_SIDE, 2 * reach + 2)


def _primary_area() -> QRect:
    screen = QApplication.primaryScreen()
    return screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)


class CrosshairOverlay(QWidget):
    """The crosshair window. `centre` is the crosshair's screen position (global px)."""

    position_committed = Signal(QPoint)  # emitted when a drag ends (use it to save)

    def __init__(self, config: CrosshairConfig, centre: QPoint | None = None):
        # Always a top-level window with no parent or owner. See the module docstring.
        super().__init__(None, self._flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._cfg = config
        self._editing = False
        self._centre = QPoint(centre) if centre is not None else _primary_area().center()
        self._drag_origin: QPoint | None = None
        self._drag_start: QPoint | None = None
        self._apply_mode()

    @staticmethod
    def _flags() -> Qt.WindowType:
        return (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

    def nativeEvent(self, eventType, message):  # noqa: N802 - Qt naming
        """Refuse click-activation on Windows; every other message is not handled here."""
        if bytes(eventType) == _WINDOWS_MSG_EVENT and message:
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            reply = native_reply(int(msg.message))
            if reply is not None:
                return reply
        return False, 0  # not handled: Qt continues with its normal processing

    # -- state -----------------------------------------------------------------
    @property
    def editing(self) -> bool:
        return self._editing

    @property
    def centre(self) -> QPoint:
        return QPoint(self._centre)

    def set_config(self, config: CrosshairConfig) -> None:
        self._cfg = config
        self._place()
        self.update()

    def set_editing(self, on: bool) -> None:
        """Switch between locked (click-through) and edit (draggable). Caller shows/hides."""
        on = bool(on)
        if on == self._editing:
            return
        self._editing = on
        visible = self.isVisible()
        self._apply_mode()
        if visible:
            self.show()
        self.update()

    def move_centre(self, centre: QPoint) -> None:
        """Move the crosshair. Clamped so the whole window stays on the primary display."""
        self._centre = QPoint(centre)
        self._place()
        self.update()

    def reset_centre(self) -> None:
        self.move_centre(_primary_area().center())

    # -- internals -------------------------------------------------------------
    def _side(self) -> int:
        base = overlay_side(self._cfg)
        return max(base, _EDIT_MIN_SIDE) if self._editing else base

    def _apply_mode(self) -> None:
        click_through = not self._editing
        flags = self._flags()
        if click_through:
            flags |= Qt.WindowType.WindowTransparentForInput
        # Click-through is the WindowTransparentForInput window flag only. Qt ties the
        # WA_TransparentForMouseEvents attribute to that flag, so setting both is unreliable.
        # Set the whole flag set at once.
        self.setWindowFlags(flags)
        self._place()

    def _place(self) -> None:
        side = self._side()
        area = _primary_area()
        half = side // 2
        x = min(max(self._centre.x(), area.left() + half), area.right() - half)
        y = min(max(self._centre.y(), area.top() + half), area.bottom() - half)
        self._centre = QPoint(x, y)
        self.setGeometry(QRect(x - half, y - half, side, side))

    # -- mouse (edit mode only; locked windows never receive mouse events) -----
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._editing and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_start = QPoint(self._centre)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._drag_origin is not None and self._drag_start is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.move_centre(self._drag_start + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._drag_origin is not None:
            self._drag_origin = None
            self._drag_start = None
            self.position_committed.emit(self.centre)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- painting ----------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._editing:
            # A faint, dashed frame so the movable area is visible on any background.
            painter.fillRect(self.rect(), QColor(0, 0, 0, 60))
            pen = QPen(QColor(255, 255, 255, 170), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        half = self.width() / 2
        _draw_crosshair(painter, half, half, self._cfg)
        painter.end()
