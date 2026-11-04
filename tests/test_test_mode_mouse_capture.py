"""Win32 capture protocol exercised with an in-process fake API, no OS cursor changes."""
from __future__ import annotations

import ctypes

import pytest
from fps_coach.ui.test_mouse_capture import CaptureError, Msg, RawInputDevice, RawMouse, Rect


class FakeAPI:
    def __init__(self):
        self.current = RawInputDevice(1, 2, 0x100, 1234)
        self.initial = (self.current.usage_page, self.current.usage, self.current.flags, self.current.target)
        self.rect = None
        self.has_focus = True
        self.has_capture = True
        self.mouse = None
        self.fail_clip = False
        self.calls = []

    def previous_registration(self):
        return self.current

    def register(self, device):
        self.calls.append(("register", device.flags, device.target))
        self.current = None if device.flags == 1 else RawInputDevice(device.usage_page, device.usage, device.flags, device.target)

    def center_rect(self, hwnd):
        return Rect(200, 300, 201, 301)

    def clip(self, rect):
        self.calls.append(("clip", rect is not None))
        if self.fail_clip and rect is not None:
            raise CaptureError("clip rejected")
        self.rect = rect

    def clip_matches(self, rect):
        return self.rect is not None and (self.rect.left, self.rect.top, self.rect.right, self.rect.bottom) == (rect.left, rect.top, rect.right, rect.bottom)

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


_KEEP_ALIVE = []


def _dialog_with_api(qapp, monkeypatch, api):
    from fps_coach.ui import test_mode as ui
    from fps_coach.ui.test_mouse_capture import RawMouseCapture

    monkeypatch.setattr(ui, "RawMouseCapture", lambda arena, move, click, lost: RawMouseCapture(arena, move, click, lost, api=api))
    dialog = ui.TestModeDialog(lambda: None)
    _KEEP_ALIVE.append(dialog)
    dialog.show()
    qapp.processEvents()
    return dialog


def _send_raw(capture, api, dx=0, dy=0, buttons=0, flags=0):
    api.mouse = RawMouse()
    api.mouse.dx, api.mouse.dy = dx, dy
    api.mouse.flags = flags
    api.mouse.detail.flags = buttons
    msg = Msg()
    msg.hwnd = capture._hwnd
    msg.message = 0x00FF
    msg.lparam = 9
    assert capture.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)


def test_capture_protocol_raw_first_packet_and_click_order(qapp, monkeypatch):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QWidget

    api = FakeAPI()
    dialog = _dialog_with_api(qapp, monkeypatch, api)
    dialog.start_comparison()
    arena, capture = dialog.arena, dialog.arena._capture
    assert arena.active and capture._active
    assert QWidget.mouseGrabber() is arena
    assert api.rect is not None
    original = arena.simulation.positions[0]
    _send_raw(capture, api, dx=12, dy=-4, buttons=1)
    assert arena.simulation.positions == pytest.approx([(original[0] + 12, original[1] - 4)] * 2)
    assert [s.attempts for s in arena.simulation.stats] == [1, 1]
    p = QPointF(10, 10)
    # Legacy Qt click for the same physical press cannot score a second time.
    arena.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, p, p, Qt.MouseButton.LeftButton,
                                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert [s.attempts for s in arena.simulation.stats] == [1, 1]
    dialog.stop_comparison()
    assert not capture._active and api.rect is None and QWidget.mouseGrabber() is None
    assert (api.current.usage_page, api.current.usage, api.current.flags, api.current.target) == api.initial
    dialog.close()


def test_clip_failure_rolls_back_registration_and_mouse_grab(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget

    api = FakeAPI()
    api.fail_clip = True
    dialog = _dialog_with_api(qapp, monkeypatch, api)
    dialog.start_comparison()
    assert not dialog.arena.active and dialog.arena.simulation is None
    assert QWidget.mouseGrabber() is None
    assert (api.current.usage_page, api.current.usage, api.current.flags, api.current.target) == api.initial
    assert "clip rejected" in dialog.stats_label.text()
    dialog.close()


@pytest.mark.parametrize("failure", ["focus", "clip", "absolute", "grab"])
def test_capture_loss_immediately_stops_and_discards_input(qapp, monkeypatch, failure):
    api = FakeAPI()
    dialog = _dialog_with_api(qapp, monkeypatch, api)
    dialog.start_comparison()
    arena, capture = dialog.arena, dialog.arena._capture
    positions = list(arena.simulation.positions)
    if failure == "focus":
        api.has_focus = False
    if failure == "grab":
        api.has_capture = False
    if failure == "clip":
        # Another app changed the shared clip: we must not clear its new clip.
        api.rect = Rect(10, 10, 20, 20)
    _send_raw(capture, api, dx=5, buttons=1, flags=1 if failure == "absolute" else 0)
    assert not arena.active and not capture._active
    assert arena.simulation.positions == positions
    assert arena.simulation.stats[0].attempts == 0
    if failure == "clip":
        assert api.rect.left == 10
    else:
        assert api.rect is None
    dialog.close()


def test_non_windows_implementation_fails_closed(qapp, monkeypatch):
    import fps_coach.ui.test_mouse_capture as mouse

    monkeypatch.setattr(mouse.sys, "platform", "linux")
    with pytest.raises(CaptureError, match="requires Windows"):
        mouse.WindowsMouseAPI()


def test_raw_struct_layout_matches_windows_abi():
    from fps_coach.ui.test_mouse_capture import RawHeader, RawInput, RawInputDevice, RawMouse
    assert ctypes.sizeof(RawMouse) == 24
    assert RawMouse.dx.offset == 12 and RawMouse.dy.offset == 16
    assert ctypes.sizeof(RawHeader) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
    assert RawInput.mouse.offset == ctypes.sizeof(RawHeader)
    assert ctypes.sizeof(RawInputDevice) == (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12)


def test_winapi_raw_parser_preserves_signed_counts_and_button_flags(monkeypatch):
    from fps_coach.ui import test_mouse_capture as mouse

    class Function:
        def __init__(self, implementation):
            self.implementation = implementation
        def __call__(self, *args):
            return self.implementation(*args)

    raw = mouse.RawInput()
    raw.header.type = 0
    raw.header.size = ctypes.sizeof(raw)
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
            for name in ("RegisterRawInputDevices", "GetRegisteredRawInputDevices", "GetRawInputData",
                         "GetClientRect", "ClientToScreen", "ClipCursor", "GetClipCursor", "GetCapture",
                         "GetForegroundWindow"):
                setattr(self, name, Function(get_data if name == "GetRawInputData" else lambda *args: 1))

    monkeypatch.setattr(mouse.sys, "platform", "win32")
    monkeypatch.setattr(mouse.ctypes, "WinDLL", lambda *args, **kwargs: Library(), raising=False)
    api = mouse.WindowsMouseAPI()
    packet = api.read_mouse(7)
    assert (packet.dx, packet.dy, packet.detail.flags) == (-130, 250, 1)
    raw.header.type = 1
    assert api.read_mouse(7) is None


def test_failed_os_grab_refuses_to_start_and_restores_registration(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget

    api = FakeAPI()
    api.has_capture = False
    dialog = _dialog_with_api(qapp, monkeypatch, api)
    dialog.start_comparison()
    assert not dialog.arena.active and api.rect is None
    assert QWidget.mouseGrabber() is None
    assert api.current.flags == api.initial[2] and api.current.target == api.initial[3]
    assert "could not capture" in dialog.stats_label.text()
    dialog.close()


def test_closing_active_dialog_releases_capture_and_registration(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget

    api = FakeAPI()
    dialog = _dialog_with_api(qapp, monkeypatch, api)
    dialog.start_comparison()
    assert dialog.arena.active and api.rect is not None
    dialog.close()
    assert api.rect is None and QWidget.mouseGrabber() is None
    assert api.current.flags == api.initial[2] and api.current.target == api.initial[3]
    assert not dialog.arena.active


def test_emergency_recenter_does_not_unclip_mouse(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    api = FakeAPI()
    dialog = _dialog_with_api(qapp, monkeypatch, api)
    dialog.start_comparison()
    arena = dialog.arena
    _send_raw(arena._capture, api, dx=30, dy=20)
    QTest.keyClick(dialog, Qt.Key.Key_R)
    assert arena.simulation.positions == [(arena.width() / 2, arena.height() / 2)] * 2
    assert arena.active and api.rect is not None
    dialog.close()
