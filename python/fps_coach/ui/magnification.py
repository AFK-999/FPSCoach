"""Optional Windows desktop magnification and single-key toggle support.

No process memory access, input hooks or injection. The only background input
observation is polling the single explicitly selected virtual key while armed.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from functools import lru_cache

from PySide6.QtCore import QObject, Qt, QTimer, Signal

# Physical mouse buttons (not logical left/right swaps), common keyboard keys.
BINDINGS = [("None", 0), ("Mouse · left (physical)", 0x01), ("Mouse · right (physical)", 0x02),
            ("Mouse · middle", 0x04), ("Mouse · side 1", 0x05), ("Mouse · side 2", 0x06)]
BINDINGS += [(f"Keyboard · F{i}", 0x6F + i) for i in range(1, 25)]
BINDINGS += [(f"Keyboard · {chr(i)}", i) for i in range(ord("A"), ord("Z") + 1)]
BINDINGS += [(f"Keyboard · {i}", ord(str(i))) for i in range(10)]
BINDINGS += [(f"Keyboard · Numpad {i}", 0x60 + i) for i in range(10)]
BINDINGS += [
    ("Keyboard · Space", 0x20), ("Keyboard · Enter", 0x0D),
    ("Keyboard · Escape", 0x1B), ("Keyboard · Tab", 0x09),
    ("Keyboard · Backspace", 0x08), ("Keyboard · Insert", 0x2D),
    ("Keyboard · Delete", 0x2E), ("Keyboard · Home", 0x24),
    ("Keyboard · End", 0x23), ("Keyboard · Page Up", 0x21),
    ("Keyboard · Page Down", 0x22), ("Keyboard · Left arrow", 0x25),
    ("Keyboard · Up arrow", 0x26), ("Keyboard · Right arrow", 0x27),
    ("Keyboard · Down arrow", 0x28), ("Keyboard · Left Shift", 0xA0),
    ("Keyboard · Right Shift", 0xA1), ("Keyboard · Left Ctrl", 0xA2),
    ("Keyboard · Right Ctrl", 0xA3), ("Keyboard · Left Alt", 0xA4),
    ("Keyboard · Right Alt", 0xA5), ("Keyboard · Caps Lock", 0x14),
    ("Keyboard · Num Lock", 0x90), ("Keyboard · Scroll Lock", 0x91),
    ("Keyboard · Pause", 0x13), ("Keyboard · Print Screen", 0x2C),
    ("Keyboard · Numpad multiply", 0x6A), ("Keyboard · Numpad plus", 0x6B),
    ("Keyboard · Numpad minus", 0x6D), ("Keyboard · Numpad decimal", 0x6E),
    ("Keyboard · Numpad divide", 0x6F),
    ("Keyboard · ; (US layout)", 0xBA), ("Keyboard · = (US layout)", 0xBB),
    ("Keyboard · , (US layout)", 0xBC), ("Keyboard · - (US layout)", 0xBD),
    ("Keyboard · . (US layout)", 0xBE), ("Keyboard · / (US layout)", 0xBF),
    ("Keyboard · ` (US layout)", 0xC0), ("Keyboard · [ (US layout)", 0xDB),
    ("Keyboard · \\ (US layout)", 0xDC), ("Keyboard · ] (US layout)", 0xDD),
    ("Keyboard · ' (US layout)", 0xDE),
]
ALLOWED_KEYS = {code for _, code in BINDINGS}

# A responsive target, not a real-time guarantee. This timer shares the Qt
# GUI thread with screen capture and can be delayed by slow grabs or repainting.
KEY_POLL_INTERVAL_MS = 5


def windows_available() -> bool:
    """Windows 10 2004+ is required for overlay capture exclusion."""
    return sys.platform == "win32" and sys.getwindowsversion().build >= 19041


@lru_cache(maxsize=1)
def _user32():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowDisplayAffinity.restype = wintypes.BOOL
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = ctypes.c_long
    user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user.SetWindowLongW.restype = ctypes.c_long
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    return user


_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_ERROR_NOT_ENOUGH_MEMORY = 8


def _window_long(user, hwnd, value=None) -> int:
    # A zero return may be a valid previous value. Win32 requires clearing the
    # thread-local last error before each call to disambiguate it from failure.
    ctypes.set_last_error(0)
    if value is None:
        result = user.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    else:
        result = user.SetWindowLongW(hwnd, _GWL_EXSTYLE, value)
    error = ctypes.get_last_error()
    if result == 0 and error:
        raise OSError(f"Windows window-style error {error}")
    return int(result)


def _retry_affinity_without_layered(user, hwnd, flag: int) -> tuple[bool, str]:
    """Retry error 8 before layering, then restore *all* original style bits.

    This workaround is required on some DWM/Qt combinations, not a promise
    that every driver or capture path will honor display affinity. The HWND is
    never recreated and the overlay's Qt repaint is requested by the caller.
    """
    try:
        style = _window_long(user, hwnd)
        if not style & _WS_EX_LAYERED:
            return False, "Windows error 8; the overlay is not layered, so the compatibility retry cannot be used."
        _window_long(user, hwnd, style & ~_WS_EX_LAYERED)
    except (OSError, AttributeError, ValueError) as exc:
        return False, f"Windows error 8; could not prepare the overlay compatibility retry: {exc}"
    result, error, restore_error = False, 0, None
    try:
        result = bool(user.SetWindowDisplayAffinity(hwnd, flag))
        if not result:
            error = ctypes.get_last_error()
    except (OSError, AttributeError, ValueError) as exc:
        restore_error = f"compatibility retry failed: {exc}"
    finally:
        try:
            _window_long(user, hwnd, style)
        except (OSError, AttributeError, ValueError) as exc:
            restore_error = f"could not restore the overlay window style: {exc}"
    if restore_error:
        # No capture is permitted after a partially completed transaction.
        try:
            user.SetWindowDisplayAffinity(hwnd, 0)
        except (OSError, AttributeError, ValueError):
            pass
        return False, f"Windows error 8; {restore_error}. Restart CurveCoach to recreate the overlay."
    if not result:
        return False, f"Windows error 8; the compatibility retry also failed (error {error})."
    return True, ""


def set_capture_exclusion(hwnd: int, enabled: bool) -> tuple[bool, str]:
    """Set and verify capture exclusion, including a layered-window error-8 retry.

    Requires DWM and Win10 2004+. Failure prevents capture; Windows/API
    acceptance does not guarantee every capture path respects exclusion.
    """
    if not windows_available():
        return False, "Dynamic magnification requires Windows 10 version 2004 or newer."
    try:
        user = _user32()
        handle = wintypes.HWND(hwnd)
        flag = 0x11 if enabled else 0
        if not user.SetWindowDisplayAffinity(handle, flag):
            error = ctypes.get_last_error()
            if error != _ERROR_NOT_ENOUGH_MEMORY:
                return False, f"Windows could not {'exclude' if enabled else 'restore'} the overlay (error {error})."
            ok, message = _retry_affinity_without_layered(user, handle, flag)
            if not ok:
                return False, message
        value = wintypes.DWORD()
        if not user.GetWindowDisplayAffinity(handle, ctypes.byref(value)) or value.value != flag:
            if enabled:
                user.SetWindowDisplayAffinity(handle, 0)
            return False, "Windows did not confirm the requested overlay capture setting."
        return True, ""
    except (OSError, AttributeError, ValueError) as exc:
        return False, f"Windows capture exclusion is unavailable: {exc}"


def key_is_down(vk: int) -> bool:
    if not windows_available() or vk not in ALLOWED_KEYS or not vk:
        return False
    return bool(_user32().GetAsyncKeyState(vk) & 0x8000)


class ToggleKeyPoller(QObject):
    """Poll one selected key; edge-trigger once after a release, never consume it."""
    pressed = Signal()
    failed = Signal(str)

    def __init__(self, parent=None, reader=None):
        super().__init__(parent)
        self._reader = reader or key_is_down
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(KEY_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)
        self._key = 0
        self._ready = False
        self._was_down = False

    def start(self, key: int) -> bool:
        self.stop()
        if key not in ALLOWED_KEYS or key == 0:
            return False
        self._key = key
        # Establish the initial up/down state now, not on the first timer tick.
        # A key held while arming still must be released before it can toggle.
        self._poll()
        if not self._key:  # The initial read may have failed and disarmed us.
            return False
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        self._key = 0
        self._ready = False
        self._was_down = False

    def _poll(self) -> None:
        if not self._key:
            return
        try:
            down = bool(self._reader(self._key))
        except Exception as exc:
            self.stop()
            self.failed.emit(f"Toggle-key monitoring stopped: {exc}")
            return
        if not down:
            self._ready = True
        if down and self._ready and not self._was_down:
            self.pressed.emit()
        self._was_down = down
