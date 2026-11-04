"""On-screen crosshair overlay: a transparent window drawn on the primary display.

Off by default. Two states:

* Locked (the normal state): the window is click-through (it ignores the mouse, so
  clicks reach whatever is underneath) and cannot be moved.
* Edit: the window accepts the mouse so the user can drag the crosshair to the spot
  they want. A dashed frame shows the area. Lock again to fix the position.

The normal crosshair only draws. Optional magnification captures pixels from a small
primary-screen region while enabled; it does not read process memory, inject input or
take keyboard focus. The overlay is excluded from supported Windows screen captures
while magnification is active to reduce recursive capture.

Focus: the overlay is a top-level window with no owner. An owned window (a child of the
main window) can pull activation back to its owner on Windows, so it must not have one.
In both states it also refuses to be activated by a click (WM_MOUSEACTIVATE returns
MA_NOACTIVATE). Dragging it therefore keeps focus where it was, and the AI Coach window
does not take it back.
"""

from __future__ import annotations

import ctypes
import math
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from fps_coach.crosshair import CrosshairConfig

from .magnification import set_capture_exclusion
from .widgets import _draw_crosshair

_MIN_SIDE = 32
_EDIT_MIN_SIDE = 200  # larger target area while editing, so it is easy to grab
# 16 ms requests a 62.5 Hz capture cadence. Qt and desktop capture are not
# real-time: slow grabs, painting and the compositor can lower the actual rate.
_MAGNIFICATION_INTERVAL_MS = 16

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
    magnification_stopped = Signal(str)
    overlay_visibility_changed = Signal(bool)

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
        self._magnification_enabled = False
        self._excluded_hwnd: int | None = None
        self._magnification_diameter = 240
        self._magnification_zoom = 2.0
        self._magnified_frame = QPixmap()
        self._source_rect = QRectF()
        self._captured_rect = QRect()
        self._lens_cache_key: tuple[int, int] | None = None
        self._lens_cache_rect = QRectF()
        self._lens_cache_path = QPainterPath()
        self._magnification_timer = QTimer(self)
        self._magnification_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._magnification_timer.setInterval(_MAGNIFICATION_INTERVAL_MS)
        self._magnification_timer.timeout.connect(self._capture_magnification)
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
        if on and self._magnification_enabled:
            self.stop_magnification()
            self.magnification_stopped.emit("Magnification stopped while editing the overlay position.")
        self._editing = on
        visible = self.isVisible()
        self._apply_mode()
        if visible:
            self.show()
        self.update()

    def move_centre(self, centre: QPoint) -> None:
        """Move the crosshair, clamping its normal overlay size to the primary display."""
        self._centre = QPoint(centre)
        self._place()
        self.update()

    def reset_centre(self) -> None:
        self.move_centre(_primary_area().center())

    @property
    def magnification_enabled(self) -> bool:
        return self._magnification_enabled

    def set_magnification_options(self, diameter: int, zoom: float) -> None:
        self._magnification_diameter = min(600, max(80, int(diameter)))
        self._magnification_zoom = min(5.0, max(1.0, float(zoom)))
        if self._magnification_enabled:
            self._place()
            self._capture_magnification()

    def start_magnification(self, diameter: int, zoom: float) -> tuple[bool, str]:
        """Start opt-in capture after excluding this overlay from supported grabs."""
        if not self.isVisible() or self._editing:
            return False, "Turn on the crosshair overlay and lock its position first."
        if self._magnification_enabled:
            self.set_magnification_options(diameter, zoom)
            return self._magnification_enabled, "" if self._magnification_enabled else "Screen capture failed."
        self.set_magnification_options(diameter, zoom)
        hwnd = int(self.winId())
        ok, message = set_capture_exclusion(hwnd, True)
        # A Windows error-8 retry may briefly change the native layered style.
        # Repaint even on failure to restore the transparent crosshair surface.
        self.update()
        if not ok:
            return False, message
        self._excluded_hwnd = hwnd
        self._magnification_enabled = True
        self._place()
        if not self._capture_magnification():
            return False, "Screen capture failed. Protected or exclusive-fullscreen content may not be available."
        self._magnification_timer.start()
        self.update()
        return True, ""

    def stop_magnification(self) -> None:
        self._magnification_timer.stop()
        was_active = self._magnification_enabled
        self._magnification_enabled = False
        self._magnified_frame = QPixmap()  # no screen frames are retained while off
        self._source_rect = QRectF()
        self._captured_rect = QRect()
        if was_active:
            # Affinity belongs to an HWND, not the QWidget. Never modify a new
            # handle if Qt or Windows recreated the original native window.
            hwnd = int(self.winId())
            ok, message = (set_capture_exclusion(hwnd, False)
                           if hwnd == self._excluded_hwnd else (True, ""))
            self._excluded_hwnd = None
            self._place()
            self.update()
            if not ok:
                self.magnification_stopped.emit(f"Magnification stopped, but capture exclusion could not be restored: {message}")
        self.update()

    def _capture_magnification(self) -> bool:
        if not self._magnification_enabled:
            return False
        if int(self.winId()) != self._excluded_hwnd:
            self.stop_magnification()
            self.magnification_stopped.emit("The overlay window changed. Magnification stopped; turn it on again.")
            return False
        screen = QApplication.primaryScreen()
        if screen is None:
            self.stop_magnification()
            self.magnification_stopped.emit("No primary screen is available for magnification.")
            return False
        # Keep both the requested zoom and the crosshair centre exact even
        # when diameter/zoom is fractional. The integer grab encloses that
        # logical-pixel source square; painting clips the extra edge pixels.
        sample_side = self._magnification_diameter / self._magnification_zoom
        source = QRectF(self._centre.x() - sample_side / 2, self._centre.y() - sample_side / 2,
                        sample_side, sample_side)
        x0, y0 = math.floor(source.left()), math.floor(source.top())
        x1, y1 = math.ceil(source.right()), math.ceil(source.bottom())
        requested = QRect(x0, y0, x1 - x0, y1 - y0)
        screen_rect = screen.geometry()
        clipped = requested.intersected(screen_rect)
        try:
            frame = (screen.grabWindow(0, clipped.x() - screen_rect.x(), clipped.y() - screen_rect.y(),
                                       clipped.width(), clipped.height()) if not clipped.isEmpty() else QPixmap())
        except Exception as exc:
            self.stop_magnification()
            self.magnification_stopped.emit(f"Screen capture failed: {exc}")
            return False
        if frame.isNull():
            self.stop_magnification()
            self.magnification_stopped.emit("Screen capture failed. Protected or exclusive-fullscreen content may not be available.")
            return False
        # Treat pixmap dimensions as physical source pixels when drawing into
        # the logical-pixel destination; Qt scales for the target display.
        frame.setDevicePixelRatio(1.0)
        self._magnified_frame = frame
        self._source_rect, self._captured_rect = source, clipped
        self.update()
        return True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.overlay_visibility_changed.emit(True)

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._magnification_enabled:
            self.stop_magnification()
            self.magnification_stopped.emit("Magnification stopped because the overlay was hidden.")
        super().hideEvent(event)
        self.overlay_visibility_changed.emit(False)

    # -- internals -------------------------------------------------------------
    def _side(self) -> int:
        base = overlay_side(self._cfg)
        base = max(base, _EDIT_MIN_SIDE) if self._editing else base
        side = max(base, self._magnification_diameter + 4) if self._magnification_enabled else base
        return side + side % 2  # even size keeps the painter and screen centre identical

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
        # Growing the lens must not shift the crosshair; at display edges the
        # circular overlay/capture is clipped rather than moving its centre.
        clamp_half = (max(overlay_side(self._cfg), _EDIT_MIN_SIDE) if self._editing else overlay_side(self._cfg)) // 2 if self._magnification_enabled else half
        x = min(max(self._centre.x(), area.left() + clamp_half), area.right() - clamp_half)
        y = min(max(self._centre.y(), area.top() + clamp_half), area.bottom() - clamp_half)
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
        if self._magnification_enabled and not self._magnified_frame.isNull():
            diameter = float(self._magnification_diameter)
            half = self.width() / 2
            key = (self.width(), self._magnification_diameter)
            if key != self._lens_cache_key:
                self._lens_cache_key = key
                self._lens_cache_rect = QRectF(half - diameter / 2, half - diameter / 2, diameter, diameter)
                self._lens_cache_path = QPainterPath()
                self._lens_cache_path.addEllipse(self._lens_cache_rect)
            lens = self._lens_cache_rect
            source, clipped = self._source_rect, self._captured_rect
            scale = diameter / max(1, source.width())
            dest = QRectF(lens.left() + (clipped.left() - source.left()) * scale,
                          lens.top() + (clipped.top() - source.top()) * scale,
                          clipped.width() * scale, clipped.height() * scale)
            painter.save()
            painter.setClipPath(self._lens_cache_path)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(dest, self._magnified_frame, QRectF(self._magnified_frame.rect()))
            painter.restore()
            painter.setPen(QPen(QColor(255, 255, 255, 190), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(lens)
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
