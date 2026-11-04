"""Temporary foreground-only Windows raw mouse capture for Test Mode.

No keyboard hooks or background input. Cursor clipping is a shared OS resource:
release on every exit path and stop immediately if the clip/focus changes.
"""
from __future__ import annotations

import atexit
import ctypes
import sys
from collections.abc import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtWidgets import QApplication, QWidget

U16 = ctypes.c_uint16
U32 = ctypes.c_uint32
I32 = ctypes.c_int32
PTR = ctypes.c_void_p
UPTR = ctypes.c_size_t
IPTR = ctypes.c_ssize_t


class Point(ctypes.Structure):
    _fields_ = [("x", I32), ("y", I32)]


class Rect(ctypes.Structure):
    _fields_ = [("left", I32), ("top", I32), ("right", I32), ("bottom", I32)]


class Msg(ctypes.Structure):
    _fields_ = [("hwnd", PTR), ("message", U32), ("wparam", UPTR), ("lparam", IPTR),
                ("time", U32), ("point", Point), ("private", U32)]


class RawInputDevice(ctypes.Structure):
    _fields_ = [("usage_page", U16), ("usage", U16), ("flags", U32), ("target", PTR)]


class RawHeader(ctypes.Structure):
    _fields_ = [("type", U32), ("size", U32), ("device", PTR), ("wparam", UPTR)]


class MouseButtons(ctypes.Structure):
    _fields_ = [("flags", U16), ("data", U16)]


class ButtonUnion(ctypes.Union):
    _fields_ = [("buttons", U32), ("detail", MouseButtons)]


class RawMouse(ctypes.Structure):
    _anonymous_ = ("button_union",)
    _fields_ = [("flags", U16), ("button_union", ButtonUnion), ("raw_buttons", U32),
                ("dx", I32), ("dy", I32), ("extra", U32)]


class RawInput(ctypes.Structure):
    _fields_ = [("header", RawHeader), ("mouse", RawMouse)]


class CaptureError(RuntimeError):
    """Safe, user-visible failure to obtain or maintain capture."""


class WindowsMouseAPI:
    """Small Win32 adapter; pointer-sized signatures are explicit on 64-bit Windows."""

    def __init__(self):
        if sys.platform != "win32":
            raise CaptureError("Mouse capture requires Windows; Test Mode cannot safely start on this platform.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        u = self.user32
        u.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RawInputDevice), U32, U32]
        u.RegisterRawInputDevices.restype = ctypes.c_int
        u.GetRegisteredRawInputDevices.argtypes = [ctypes.POINTER(RawInputDevice), ctypes.POINTER(U32), U32]
        u.GetRegisteredRawInputDevices.restype = U32
        u.GetRawInputData.argtypes = [PTR, U32, PTR, ctypes.POINTER(U32), U32]
        u.GetRawInputData.restype = U32
        u.GetClientRect.argtypes = [PTR, ctypes.POINTER(Rect)]
        u.GetClientRect.restype = ctypes.c_int
        u.ClientToScreen.argtypes = [PTR, ctypes.POINTER(Point)]
        u.ClientToScreen.restype = ctypes.c_int
        u.ClipCursor.argtypes = [ctypes.POINTER(Rect)]
        u.ClipCursor.restype = ctypes.c_int
        u.GetClipCursor.argtypes = [ctypes.POINTER(Rect)]
        u.GetClipCursor.restype = ctypes.c_int
        u.GetCapture.argtypes = []
        u.GetCapture.restype = PTR
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = PTR

    def previous_registration(self) -> RawInputDevice | None:
        count = U32(0)
        failed = 0xFFFFFFFF
        if self.user32.GetRegisteredRawInputDevices(None, ctypes.byref(count), ctypes.sizeof(RawInputDevice)) == failed:
            raise CaptureError("Could not inspect existing raw-input registrations.")
        if not count.value:
            return None
        devices = (RawInputDevice * count.value)()
        result = self.user32.GetRegisteredRawInputDevices(devices, ctypes.byref(count), ctypes.sizeof(RawInputDevice))
        if result == failed:
            raise CaptureError("Could not inspect existing raw-input registrations.")
        for item in devices[:result]:
            if item.usage_page == 1 and item.usage == 2:
                return RawInputDevice(item.usage_page, item.usage, item.flags, item.target)
        return None

    def register(self, device: RawInputDevice) -> None:
        if not self.user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
            raise CaptureError("Windows could not register raw mouse input.")

    def center_rect(self, hwnd: int) -> Rect:
        rect, top_left = Rect(), Point(0, 0)
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)) or not self.user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
            raise CaptureError("Windows could not locate the test arena.")
        if rect.right <= rect.left or rect.bottom <= rect.top:
            raise CaptureError("The test arena is not visible.")
        x = top_left.x + (rect.right - rect.left) // 2
        y = top_left.y + (rect.bottom - rect.top) // 2
        return Rect(x, y, x + 1, y + 1)

    def clip(self, rect: Rect | None) -> None:
        if not self.user32.ClipCursor(ctypes.byref(rect) if rect is not None else None):
            raise CaptureError("Windows could not change cursor confinement.")

    def clip_matches(self, rect: Rect) -> bool:
        current = Rect()
        if not self.user32.GetClipCursor(ctypes.byref(current)):
            raise CaptureError("Windows could not verify cursor confinement.")
        return _rect_values(current) == _rect_values(rect)

    def foreground(self, hwnd: int) -> bool:
        return self.user32.GetForegroundWindow() == hwnd

    def mouse_captured(self, hwnd: int) -> bool:
        return self.user32.GetCapture() == hwnd

    def read_mouse(self, handle: int) -> RawMouse | None:
        size = U32(0)
        result = self.user32.GetRawInputData(handle, 0x10000003, None, ctypes.byref(size), ctypes.sizeof(RawHeader))
        if result == 0xFFFFFFFF or size.value < ctypes.sizeof(RawHeader) or size.value > 65536:
            raise CaptureError("Unable to read raw mouse input.")
        buf = ctypes.create_string_buffer(size.value)
        result = self.user32.GetRawInputData(handle, 0x10000003, buf, ctypes.byref(size), ctypes.sizeof(RawHeader))
        if result == 0xFFFFFFFF or result < ctypes.sizeof(RawHeader):
            raise CaptureError("Unable to read raw mouse input.")
        header = RawHeader.from_buffer_copy(buf.raw)
        if header.type != 0:
            return None
        if result < ctypes.sizeof(RawInput):
            raise CaptureError("Incomplete raw mouse input.")
        raw = RawInput.from_buffer_copy(buf.raw)
        return raw.mouse if raw.header.type == 0 else None


# ctypes structures are not iterable by default.
def _rect_values(rect: Rect) -> tuple[int, int, int, int]:
    return rect.left, rect.top, rect.right, rect.bottom


class RawMouseCapture(QAbstractNativeEventFilter):
    """Single active foreground capture. Escape and focus loss release the OS cursor."""

    def __init__(self, arena: QWidget, on_move: Callable[[int, int], None],
                 on_click: Callable[[], None], on_lost: Callable[[str], None], api=None):
        super().__init__()
        self.arena = arena
        self.on_move, self.on_click, self.on_lost = on_move, on_click, on_lost
        self.api = api if api is not None else WindowsMouseAPI()
        self._active = False
        self._registered = False
        self._clipped = False
        self._filter_installed = False
        self._grabbed = False
        self._quit_connected = False
        self._previous: RawInputDevice | None = None
        self._rect: Rect | None = None
        self._hwnd = 0
        self._arena_hwnd = 0
        self._timer = QTimer(arena)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._check_capture)

    def start(self) -> None:
        if self._active:
            return
        app = QApplication.instance()
        if app is None or not self.arena.isVisible() or not self.arena.window().isActiveWindow():
            raise CaptureError("Open and focus the Test Mode window before starting capture.")
        self._hwnd = int(self.arena.window().winId())
        if not self.api.foreground(self._hwnd):
            raise CaptureError("Test Mode must be the foreground window to capture the mouse.")
        self._arena_hwnd = int(self.arena.winId())
        self._rect = self.api.center_rect(self._arena_hwnd)
        self._previous = self.api.previous_registration()
        try:
            self.api.register(RawInputDevice(1, 2, 0, self._hwnd))
            self._registered = True
            app.installNativeEventFilter(self)
            self._filter_installed = True
            self.arena.grabMouse()
            self._grabbed = QWidget.mouseGrabber() is self.arena
            if not self._grabbed or not self.api.mouse_captured(self._arena_hwnd):
                raise CaptureError("Qt could not capture mouse clicks for the test arena.")
            self.api.clip(self._rect)
            self._clipped = True
            if not self.api.clip_matches(self._rect):
                raise CaptureError("Windows did not retain cursor confinement.")
            self._active = True
            self._timer.start()
            app.aboutToQuit.connect(self.stop)
            self._quit_connected = True
            atexit.register(self.stop)
        except Exception:
            self.stop()
            raise

    def stop(self) -> str | None:
        error = None
        self._active = False
        try:
            self._timer.stop()
        except RuntimeError:  # Qt may already be tearing down at process exit.
            pass
        if self._clipped:
            # Only undo our own clip; do not undo a newer clip belonging to another app.
            try:
                if self._rect is not None and self.api.clip_matches(self._rect):
                    self.api.clip(None)
            except Exception:
                try:
                    self.api.clip(None)
                except Exception:
                    error = "Windows could not release cursor confinement; switch away from Test Mode or restart the app."
            self._clipped = False
        if self._grabbed:
            try:
                if QWidget.mouseGrabber() is self.arena:
                    self.arena.releaseMouse()
            except RuntimeError:
                pass
            self._grabbed = False
        if self._filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self)
            self._filter_installed = False
        if self._registered:
            try:
                current = self.api.previous_registration()
                if current is not None and current.target == self._hwnd and current.flags == 0:
                    self.api.register(self._previous or RawInputDevice(1, 2, 1, None))
            except Exception:
                pass
            self._registered = False
        if self._quit_connected:
            try:
                app = QApplication.instance()
                if app is not None:
                    app.aboutToQuit.disconnect(self.stop)
            except RuntimeError:
                pass
            self._quit_connected = False
        atexit.unregister(self.stop)
        return error

    def _lose(self, message: str) -> None:
        error = self.stop()
        self.on_lost(error or message)

    def _check_capture(self) -> bool:
        if not self._active:
            return False
        try:
            valid = (self.arena.isVisible() and self.arena.window().isActiveWindow()
                     and self.api.foreground(self._hwnd) and self._rect is not None
                     and QWidget.mouseGrabber() is self.arena and self.api.mouse_captured(self._arena_hwnd)
                     and self.api.clip_matches(self._rect))
        except Exception:
            valid = False
        if not valid:
            self._lose("Mouse capture ended because focus or cursor confinement was lost.")
            return False
        return True

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if not self._active or bytes(event_type) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        msg = Msg.from_address(int(message))
        if msg.message != 0x00FF or msg.hwnd != self._hwnd:  # WM_INPUT
            return False, 0
        if not self._check_capture():
            return False, 0
        try:
            mouse = self.api.read_mouse(msg.lparam)
            if mouse is None:
                return False, 0
            if mouse.flags & 1:  # MOUSE_MOVE_ABSOLUTE (e.g. some remote/tablet devices)
                self._lose("This device reports absolute input; relative mouse input is required.")
                return False, 0
            if mouse.dx or mouse.dy:
                self.on_move(mouse.dx, mouse.dy)
            if mouse.detail.flags & 1:  # RI_MOUSE_LEFT_BUTTON_DOWN
                self.on_click()
        except Exception:
            self._lose("Raw mouse capture encountered an error and was released.")
        # Let Qt/Windows perform the default WM_INPUT cleanup.
        return False, 0
