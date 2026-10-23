"""Window capture: only the chosen window is recorded, and any fallback says so.

The native checks use the Linux fake desktop build when it is importable (see
native/tests/fake_dxgi/README.txt). Without it they are skipped. The Windows Graphics Capture path
itself cannot run on Linux, so it is not covered here.
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _WindowBackend:
    """A native-like stub that records how it was started."""

    native_available = True
    fast_frame_sequence = True

    def __init__(self, *, has_window_api: bool = True):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.stats = {"running": True, "capture_fps": 60.0, "frames_captured": 1,
                      "capture_width": 320, "capture_height": 180, "adapter_name": "GPU",
                      "capture_mode": "window", "notice": "", "last_frame_age_ms": 0}
        if has_window_api:
            self.start_window = self._start_window  # only an updated native build has it

    def outputs(self):
        return [{"output_name": "D1", "adapter_name": "GPU", "width": 1920, "height": 1080,
                 "adapter_index": 0, "output_index": 0}]

    def capture_stats(self):
        return dict(self.stats)

    def latest_sequence(self):
        return 0

    def latest_frame(self):
        return None

    # The native object's method is named ``start``; CoreBackend.start_capture calls it.
    def start(self, *args):
        self.calls.append(("start_capture", args, {}))

    def _start_window(self, **kwargs):
        self.calls.append(("start_window", (), kwargs))

    def stop(self):
        self.calls.append(("stop", (), {}))


# -- backend routing ---------------------------------------------------------------

def test_start_window_without_native_core_says_so() -> None:
    from fps_coach.backend import CoreBackend, NativeCoreUnavailable

    backend = CoreBackend.__new__(CoreBackend)
    backend.capture = None
    with pytest.raises(NativeCoreUnavailable):
        backend.start_window(1234, 0, 0, 320, 180, 60, 10, 20)


def test_start_window_uses_the_native_window_path_when_present() -> None:
    from fps_coach.backend import CoreBackend

    backend = CoreBackend.__new__(CoreBackend)
    backend.capture = _WindowBackend()
    note = backend.start_window(1234, 2, 1, 320, 180, 30, 10, 20)
    assert note == ""
    name, args, kwargs = backend.capture.calls[0]
    assert name == "start_window"
    assert kwargs == {"hwnd": 1234, "adapter_index": 2, "output_index": 1, "x": 10, "y": 20,
                      "width": 320, "height": 180, "target_fps": 30}


def test_start_window_on_an_older_native_build_falls_back_with_a_note() -> None:
    from fps_coach.backend import OLD_NATIVE_WINDOW_NOTE, CoreBackend

    backend = CoreBackend.__new__(CoreBackend)
    backend.capture = _WindowBackend(has_window_api=False)
    note = backend.start_window(1234, 0, 0, 320, 180, 60, 10, 20)
    assert note == OLD_NATIVE_WINDOW_NOTE
    assert "screen area" in note and "windows on top" in note
    name, args, _kwargs = backend.capture.calls[0]
    assert name == "start_capture"
    assert args == (0, 0, 10, 20, 320, 180, 60)  # the window's bounds, as a screen-area crop


# -- the page -----------------------------------------------------------------------

def _page(qapp, backend):
    from fps_coach.ui.pages_setup import CapturePage

    page = CapturePage(backend)
    page.resize(1000, 700)
    page.show()
    qapp.processEvents()
    return page


def test_frame_timer_polls_fast_enough_for_60_fps() -> None:
    from fps_coach.ui import pages_setup

    # 1000 ms / 60 = 16.7 ms per frame. The poll is a few times faster, so a new frame is
    # noticed within a few milliseconds.
    assert pages_setup.FRAME_INTERVAL_MS <= 4


def test_status_text_follows_the_capture_mode(qapp) -> None:
    from fps_coach.ui.pages_setup import CapturePage

    assert CapturePage._status_text("window") == "Capturing this window only"
    assert CapturePage._status_text("region") == "Capturing screen area"
    assert CapturePage._status_text("output") == "Capturing full display"


def test_fallback_is_shown_as_a_notice_not_an_error(qapp) -> None:
    backend = _WindowBackend()
    backend.stats.update(capture_mode="region",
                         notice="Window capture is not available in this build.")
    page = _page(qapp, backend)
    page._set_running(True)
    page._refresh_stats()
    assert page.status_label.text() == "Capturing screen area"
    assert "not available" in page.notice_label.text()
    assert not page.error_label.isVisible()


def test_stall_note_only_in_window_mode(qapp) -> None:
    backend = _WindowBackend()
    backend.stats.update(capture_mode="window", last_frame_age_ms=2500)
    page = _page(qapp, backend)
    page._set_running(True)
    page._refresh_stats()
    assert "No new frames from this window" in page.notice_label.text()

    backend.stats.update(capture_mode="region", last_frame_age_ms=2500)
    page._refresh_stats()
    assert "No new frames" not in page.notice_label.text()


def test_notice_clears_when_capture_stops(qapp) -> None:
    backend = _WindowBackend()
    backend.stats.update(notice="Showing the screen area.", capture_mode="region")
    page = _page(qapp, backend)
    page._set_running(True)
    page._refresh_stats()
    assert page.notice_label.isVisible()
    page._set_running(False)
    assert not page.notice_label.isVisible()


# -- native smoke (Linux fake desktop build only) ------------------------------------

def _fake_native():
    if sys.platform == "win32":
        pytest.skip("Windows runs the real Windows Graphics Capture path")
    native = pytest.importorskip("fpscoach_native")
    if not hasattr(native.DesktopCapture, "start_window"):
        pytest.skip("native module predates window capture")
    return native


def test_native_window_request_falls_back_to_the_screen_area_on_linux() -> None:
    native = _fake_native()
    capture = native.DesktopCapture()
    capture.start_window(hwnd=1234, width=480, height=270, target_fps=60)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and capture.stats()["frames_captured"] < 5:
            time.sleep(0.02)
        stats = capture.stats()
        assert stats["capture_mode"] == "region"
        assert "not available" in stats["notice"]
        assert stats["frames_captured"] >= 5
    finally:
        capture.stop()


def test_native_frames_arrive_before_they_are_stamped() -> None:
    native = _fake_native()
    capture = native.DesktopCapture()
    capture.start_window(hwnd=1234, width=480, height=270, target_fps=60)
    try:
        seen = set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(seen) < 10:
            frame = capture.latest_frame()
            if frame and frame["sequence"] not in seen:
                seen.add(frame["sequence"])
                assert frame["arrival_us"] > 0
                assert frame["arrival_us"] <= frame["timestamp_us"]
            time.sleep(0.005)
        assert len(seen) >= 10
    finally:
        capture.stop()


def test_selected_window_is_captured_as_a_window_with_its_bounds(qapp, monkeypatch) -> None:
    from fps_coach.ui import pages_setup

    monkeypatch.setattr(pages_setup, "get_window_rect", lambda hwnd: (100, 50, 800, 600))
    from fps_coach.backend import CoreBackend

    native = _WindowBackend()
    page = _page(qapp, native)  # outputs come from the native stub
    backend = CoreBackend.__new__(CoreBackend)  # the page talks to CoreBackend, as in the app
    backend.capture = native
    page._backend = backend
    page.source_combo.addItem("Google Chrome", 4321)
    page.source_combo.setCurrentIndex(page.source_combo.count() - 1)
    page._start()
    name, _args, kwargs = native.calls[-1]
    assert name == "start_window"
    assert kwargs["hwnd"] == 4321
    assert (kwargs["x"], kwargs["y"], kwargs["width"], kwargs["height"]) == (100, 50, 800, 600)
    assert not page.notice_label.isVisible()
    page._stop()
