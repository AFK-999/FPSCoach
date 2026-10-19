"""Enumerate visible top-level application windows (Windows only).

Used by the Capture tab so the user can restrict the preview capture to one
application's window region instead of the whole display. This only reads window
titles and on-screen rectangles via the documented Win32 API - it never attaches
to, injects into, or reads the memory of any process.

On non-Windows platforms every function degrades gracefully (returns empty / None)
so the rest of the app keeps working and the module stays importable/testable.
"""

from __future__ import annotations

import os
import sys

_IS_WINDOWS = sys.platform == "win32"


def list_windows() -> list[dict]:
    """Return visible top-level windows as dicts: {hwnd, title, pid, process}."""
    if not _IS_WINDOWS:
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    windows: list[dict] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _process_name(pid: int) -> str:
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return os.path.basename(buf.value)
                return ""
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return ""

    def _callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "pid": int(pid.value),
                    "process": _process_name(int(pid.value)),
                }
            )
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(_callback), 0)
    except Exception:
        return []
    return windows


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return (left, top, width, height) in screen coordinates, or None."""
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return (rect.left, rect.top, width, height)
    except Exception:
        return None
