"""Foreground raw calibration, fake Win32 transport, never touches actual OS input."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest
from fps_coach.backend import _FallbackAnalyzer
from fps_coach.ui.raw_mouse_capture import CaptureError, Msg, RawInputDevice, RawMouse, Rect


class FakeAPI:
    def __init__(self):
        self.current = RawInputDevice(1, 2, 0x100, 1234)
        self.rect = None
        self.has_focus = True
        self.has_capture = True
        self.mouse = None
        self.fail_clip = False

    def previous_registration(self):
        return self.current

    def register(self, device):
        self.current = (
            None
            if device.flags == 1
            else RawInputDevice(device.usage_page, device.usage, device.flags, device.target)
        )

    def center_rect(self, hwnd):
        return Rect(200, 300, 201, 301)

    def clip(self, rect):
        if self.fail_clip and rect is not None:
            raise CaptureError("clip rejected")
        self.rect = rect

    def clip_matches(self, rect):
        return self.rect is not None and (
            self.rect.left,
            self.rect.top,
            self.rect.right,
            self.rect.bottom,
        ) == (rect.left, rect.top, rect.right, rect.bottom)

    def foreground(self, hwnd):
        return self.has_focus

    def mouse_captured(self, hwnd):
        return self.has_capture

    def read_mouse(self, handle):
        return self.mouse


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP = []


def _canvas(qapp, monkeypatch, api=None):
    from fps_coach.ui import raw_mouse_capture as capture_mod
    from fps_coach.ui.calibration import CalibrationCanvas

    api = api or FakeAPI()
    capture = capture_mod.RawMouseCapture
    monkeypatch.setattr(
        capture_mod,
        "RawMouseCapture",
        lambda arena, move, click, lost, **kwargs: capture(
            arena, move, click, lost, api=api, **kwargs
        ),
    )
    canvas = CalibrationCanvas(_FallbackAnalyzer())
    _KEEP.append(canvas)
    canvas.resize(650, 430)
    canvas.show()
    qapp.processEvents()
    canvas.set_input_mode("raw")
    return canvas, api


def _packet(canvas, api, dx=0, dy=0, button=0, device=9, flags=0):
    capture = canvas._raw_capture
    assert capture is not None
    api.mouse = RawMouse()
    api.mouse.dx, api.mouse.dy = dx, dy
    api.mouse.flags = flags
    api.mouse.detail.flags = button
    api.mouse.device_id = device
    msg = Msg()
    msg.hwnd, msg.message, msg.lparam = capture._arena_hwnd, 0x00FF, 11
    from shiboken6 import VoidPtr

    return canvas.nativeEvent(b"windows_generic_MSG", VoidPtr(ctypes.addressof(msg)))


def test_raw_counts_drive_neutral_virtual_cursor_and_dynamic_curve(qapp, monkeypatch):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    canvas, api = _canvas(qapp, monkeypatch)
    canvas._cursor_global = lambda: (_ for _ in ()).throw(AssertionError("desktop input used"))
    updates = []
    canvas.recommendation_updated.connect(lambda s, r: updates.append((s, r)))
    canvas.start_session(3)
    assert canvas._active and api.rect is not None and not canvas._sample_timer.isActive()
    start = QPointF(canvas._raw_pos)
    canvas._target = QPointF(start.x() + 60, start.y())
    canvas._begin_trial()
    for dx in (10, 20, 20):
        _packet(canvas, api, dx=dx)
    assert canvas._raw_pos.x() == start.x() + 50
    _packet(canvas, api, dx=10, button=1)
    assert canvas._completed_trials == 1 and len(updates) == 1
    assert updates[0][1]["captured_sample_count"] >= 3 and updates[0][1]["points"]
    assert canvas._raw_pos.x() == start.x() + 60
    pos = QPointF(canvas._raw_pos)
    canvas.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pos,
            pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert canvas._completed_trials == 1  # Qt duplicate did not score twice
    canvas.cancel_session()
    assert not canvas._active and api.rect is None and api.current.target == 1234
    canvas.close()


def test_raw_device_change_or_unknown_fails_closed_and_resume_preserves_identity(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    canvas.start_session()
    _packet(canvas, api, dx=3, device=77)
    assert canvas._raw_device == 77
    _packet(canvas, api, dx=3, device=88)
    assert canvas._paused_for_navigation and api.rect is None and canvas._raw_capture is None
    assert canvas._raw_device == 77
    canvas.resume_raw_capture()
    assert canvas._raw_capture is not None and not canvas._paused_for_navigation
    _packet(canvas, api, dx=2, device=0)
    assert canvas._paused_for_navigation and api.rect is None
    canvas.cancel_session()
    canvas.close()


def test_raw_focus_loss_and_navigation_release_and_restart(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    canvas.start_session()
    _packet(canvas, api, dx=5)
    canvas.pause_for_navigation()
    assert canvas._paused_for_navigation and api.rect is None
    canvas.resume_after_navigation()
    assert canvas._paused_for_navigation and canvas._raw_capture is None
    canvas.resume_raw_capture()
    assert not canvas._paused_for_navigation and canvas._raw_capture is not None
    api.has_focus = False
    canvas._raw_capture._check_capture()
    assert canvas._paused_for_navigation and api.rect is None
    canvas.cancel_session()
    canvas.close()


def test_start_capture_failure_does_not_reset_analyzer_or_fallback(qapp, monkeypatch):
    api = FakeAPI()
    api.fail_clip = True
    canvas, api = _canvas(qapp, monkeypatch, api)
    analyzer = canvas._analyzer
    analyzer.begin_trial(0, 0, 100, 0, 1)
    for i in range(1, 5):
        analyzer.add_sample(i * 20, 0, 1 + i * 0.01)
    assert analyzer.end_trial(100, 0, 1.05)
    hints = []
    canvas.hint_changed.connect(hints.append)
    canvas.start_session()
    assert not canvas._active and analyzer.summary()["trial_count"] == 1
    assert api.rect is None and api.current.target == 1234
    assert "could not start" in hints[-1].lower() and canvas.input_mode == "raw"
    canvas.close()


def test_no_test_mode_entry_in_calibration_ui(qapp):
    from fps_coach.ui.pages_setup import CalibrationPage
    from PySide6.QtWidgets import QPushButton

    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP.append(page)
    assert not hasattr(page, "test_mode_btn") and not hasattr(page, "_open_test_mode")
    assert all(
        "test mode" not in button.text().lower() for button in page.findChildren(QPushButton)
    )
    assert "not detected or compensated" in page.telemetry_note.text()
    page.close()


def test_absolute_input_and_escape_release_capture(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    messages = []
    canvas.hint_changed.connect(messages.append)
    canvas.start_session()
    _packet(canvas, api, flags=1)
    assert canvas._paused_for_navigation and api.rect is None
    assert "absolute" in messages[-1].lower()
    canvas.resume_raw_capture()
    canvas._escape_raw()
    assert canvas._paused_for_navigation and api.rect is None
    canvas.cancel_session()
    canvas.close()


def test_raw_window_move_releases_and_no_stale_clicks(qapp, monkeypatch):
    from PySide6.QtCore import QEvent

    canvas, api = _canvas(qapp, monkeypatch)
    canvas.start_session()
    capture = canvas._raw_capture
    canvas.eventFilter(canvas, QEvent(QEvent.Type.Move))
    assert canvas._paused_for_navigation and api.rect is None
    position = canvas._raw_pos.x()
    canvas._raw_move(100, 100)
    canvas._raw_click()
    assert canvas._raw_pos.x() == position and canvas._completed_trials == 0
    assert not capture._active
    canvas.cancel_session()
    canvas.close()


def test_raw_is_clamped_at_arena_edge_but_reverse_is_immediate(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    canvas.start_session()
    _packet(canvas, api, dx=100000)
    assert canvas._raw_pos.x() == canvas.width() - 1
    _packet(canvas, api, dx=-1)
    assert canvas._raw_pos.x() == canvas.width() - 2
    canvas.cancel_session()
    canvas.close()


def test_raw_registration_ownership_is_respected_on_release(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    canvas.start_session()
    api.current = RawInputDevice(1, 2, 0, 5678)  # another component took ownership
    canvas.cancel_session()
    assert api.rect is None and api.current.target == 5678
    canvas.close()


def test_raw_struct_and_winapi_parser_preserve_device_and_signed_counts(monkeypatch):
    from fps_coach.ui import raw_mouse_capture as mouse

    assert ctypes.sizeof(mouse.RawMouse) == 24
    assert mouse.RawMouse.dx.offset == 12 and mouse.RawMouse.dy.offset == 16
    assert ctypes.sizeof(mouse.RawHeader) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
    assert mouse.RawInput.mouse.offset == ctypes.sizeof(mouse.RawHeader)
    assert ctypes.sizeof(mouse.RawInputDevice) == (
        16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12
    )

    class Function:
        def __init__(self, implementation):
            self.implementation = implementation

        def __call__(self, *args):
            return self.implementation(*args)

    raw = mouse.RawInput()
    raw.header.type, raw.header.size, raw.header.device = 0, ctypes.sizeof(raw), 456
    raw.mouse.dx, raw.mouse.dy = -130, 250
    raw.mouse.detail.flags = 1

    def get_data(handle, command, buffer, size, header_size):
        assert command == 0x10000003 and header_size == ctypes.sizeof(mouse.RawHeader)
        ctypes.cast(size, ctypes.POINTER(mouse.U32))[0] = ctypes.sizeof(raw)
        if buffer is None:
            return 0
        ctypes.memmove(buffer, ctypes.addressof(raw), ctypes.sizeof(raw))
        return ctypes.sizeof(raw)

    class Library:
        def __init__(self):
            for name in (
                "RegisterRawInputDevices",
                "GetRegisteredRawInputDevices",
                "GetRawInputData",
                "GetClientRect",
                "ClientToScreen",
                "ClipCursor",
                "GetClipCursor",
                "GetCapture",
                "GetForegroundWindow",
            ):
                setattr(
                    self, name, Function(get_data if name == "GetRawInputData" else lambda *args: 1)
                )

    monkeypatch.setattr(mouse.sys, "platform", "win32")
    monkeypatch.setattr(mouse.ctypes, "WinDLL", lambda *args, **kwargs: Library(), raising=False)
    api = mouse.WindowsMouseAPI()
    packet = api.read_mouse(7)
    assert (packet.device_id, packet.dx, packet.dy, packet.detail.flags) == (456, -130, 250, 1)
    raw.header.type = 1
    assert api.read_mouse(7) is None


def test_ai_context_identifies_raw_path_and_no_dpi_measurement():
    from fps_coach.ai_chat import curve_context

    context = curve_context({"trial_count": 1, "input_path": "raw"}, {})
    assert "neutral in-app virtual cursor" in context
    assert "DPI drift was not measured or compensated" in context
    assert "end-to-end game input" in context


def test_non_windows_raw_api_is_unavailable(monkeypatch):
    from fps_coach.ui import raw_mouse_capture

    monkeypatch.setattr(raw_mouse_capture.sys, "platform", "linux")
    with pytest.raises(CaptureError, match="requires Windows"):
        raw_mouse_capture.WindowsMouseAPI()


def test_release_failure_is_reported_when_capture_is_lost(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    hints = []
    canvas.hint_changed.connect(hints.append)
    canvas.start_session()
    original_clip = api.clip

    def reject_release(rect):
        if rect is None:
            raise CaptureError("release denied")
        original_clip(rect)

    api.clip = reject_release
    api.has_focus = False
    canvas._raw_capture._check_capture()
    assert canvas._paused_for_navigation
    assert "could not release" in hints[-1].lower()
    canvas.resume_raw_capture()
    assert canvas._raw_capture is None
    api.clip = original_clip
    api.clip(None)  # fake cleanup, not an OS call
    canvas.cancel_session()
    canvas.close()


def test_widget_target_foreground_dispatch_and_registration_takeover(qapp, monkeypatch):
    from shiboken6 import VoidPtr

    canvas, api = _canvas(qapp, monkeypatch)
    canvas.start_session()
    capture = canvas._raw_capture
    assert api.current.target == capture._arena_hwnd
    assert api.current.flags == 0  # foreground only; no background input
    before = canvas._raw_pos.x()
    api.mouse = RawMouse()
    api.mouse.dx = 7
    api.mouse.device_id = 9
    msg = Msg()
    msg.message, msg.lparam = 0x00FF, 11
    msg.hwnd = capture._arena_hwnd + 1
    canvas.nativeEvent(b"windows_generic_MSG", VoidPtr(ctypes.addressof(msg)))
    assert canvas._raw_pos.x() == before
    msg.hwnd, msg.wparam = capture._arena_hwnd, 1  # RIM_INPUTSINK is ignored
    canvas.nativeEvent(b"windows_generic_MSG", VoidPtr(ctypes.addressof(msg)))
    assert canvas._raw_pos.x() == before
    msg.wparam = 0
    canvas.nativeEvent(b"windows_generic_MSG", VoidPtr(ctypes.addressof(msg)))
    assert canvas._raw_pos.x() == before + 7
    messages = []
    canvas.hint_changed.connect(messages.append)
    api.current = RawInputDevice(1, 2, 0, 5678)
    capture._on_timer()
    assert canvas._paused_for_navigation and api.rect is None
    assert api.current.target == 5678  # do not overwrite someone else's registration
    assert "registration changed" in messages[-1].lower()
    canvas.cancel_session()
    canvas.close()


def test_raw_no_movement_warning_and_late_recovery(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    hints = []
    canvas.hint_changed.connect(hints.append)
    canvas.start_session()
    capture = canvas._raw_capture
    capture._started_at -= 6
    capture._on_timer()
    assert "no raw movement received yet" in hints[-1].lower()
    assert capture._active  # idle is not proof of failure; do not interrupt the user
    _packet(canvas, api, dx=3)
    assert "raw movement detected" in hints[-1].lower()
    canvas.cancel_session()
    canvas.close()


def test_last_raw_motion_gets_a_trailing_repaint(qapp, monkeypatch):
    import time

    from PySide6.QtTest import QTest

    canvas, api = _canvas(qapp, monkeypatch)
    canvas.start_session()
    repaints = []
    monkeypatch.setattr(canvas, "update", lambda: repaints.append(1))
    canvas._last_repaint_at = time.perf_counter()
    _packet(canvas, api, dx=5)
    assert canvas._repaint_timer.isActive() and not repaints
    QTest.qWait(40)
    assert repaints and not canvas._repaint_timer.isActive()
    canvas.cancel_session()
    canvas.close()


def test_arena_screen_movement_is_detected_and_releases_capture(qapp, monkeypatch):
    canvas, api = _canvas(qapp, monkeypatch)
    hints = []
    canvas.hint_changed.connect(hints.append)
    canvas.start_session()
    capture = canvas._raw_capture
    api.center_rect = lambda hwnd: Rect(300, 300, 301, 301)
    capture._on_timer()
    assert canvas._paused_for_navigation and api.rect is None
    assert "arena moved" in hints[-1].lower()
    canvas.cancel_session()
    canvas.close()
