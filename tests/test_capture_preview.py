"""Capture preview: frames are pulled at display rate, only when new and only while visible."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved(monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    from fps_coach.ui import pages_setup

    store = QSettings(str(tmp_path / "capture.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_setup, "_settings", lambda: store)
    return store


def _frame(sequence: int, width: int = 4, height: int = 2, blue=10, green=20, red=30) -> dict:
    stride = width * 4
    pixel = bytes([blue, green, red, 255])
    return {
        "sequence": sequence, "width": width, "height": height, "stride": stride,
        "bgra": pixel * (width * height),
    }


class _Backend:
    """A native-like stub. ``fast`` controls whether it can report the frame number cheaply."""

    native_available = True

    def __init__(self, fast: bool = True):
        self.sequence = 0
        self.frame_pulls = 0
        self.sequence_checks = 0
        if fast:
            self.fast_frame_sequence = True

    def outputs(self):
        return [{"output_name": "D1", "adapter_name": "GPU", "width": 1920, "height": 1080,
                 "adapter_index": 0, "output_index": 0}]

    def capture_stats(self):
        return {"running": True, "capture_fps": 60.0, "frames_captured": self.sequence}

    def latest_sequence(self):
        self.sequence_checks += 1
        return self.sequence

    def latest_frame(self):
        self.frame_pulls += 1
        return _frame(self.sequence) if self.sequence else None

    def start_capture(self, *args):
        pass

    def stop_capture(self):
        pass


def _running_page(qapp, backend):
    from fps_coach.ui.pages_setup import CapturePage

    page = CapturePage(backend)
    page.resize(1000, 700)
    page.show()
    qapp.processEvents()
    page._set_running(True)
    return page


# -- the widget -----------------------------------------------------------------

def test_frame_preview_shows_the_pixels_it_is_given(qapp) -> None:
    from fps_coach.ui.pages_setup import FramePreview

    preview = FramePreview()
    assert not preview.has_frame()
    assert preview.set_frame(_frame(1)["bgra"], 4, 2, 16)
    assert preview.has_frame()
    pixel = preview._image.pixelColor(2, 1)
    assert (pixel.red(), pixel.green(), pixel.blue()) == (30, 20, 10)
    preview.grab()  # paints without error


def test_frame_preview_rejects_a_short_or_bad_buffer_and_keeps_the_old_frame(qapp) -> None:
    from fps_coach.ui.pages_setup import FramePreview

    preview = FramePreview()
    assert preview.set_frame(_frame(1)["bgra"], 4, 2, 16)
    assert not preview.set_frame(b"\x00" * 8, 4, 2, 16)  # too short for 4x2 at stride 16
    assert not preview.set_frame(_frame(2)["bgra"], 4, 2, 8)  # stride narrower than a row
    assert not preview.set_frame(_frame(2)["bgra"], 0, 2, 16)
    assert preview.has_frame()


def test_frame_preview_clears(qapp) -> None:
    from fps_coach.ui.pages_setup import FramePreview

    preview = FramePreview()
    preview.set_frame(_frame(1)["bgra"], 4, 2, 16)
    preview.clear_frame()
    assert not preview.has_frame()
    preview.grab()  # the placeholder paints


# -- pulling frames -------------------------------------------------------------

def test_no_frame_is_copied_while_the_sequence_is_unchanged(qapp, saved) -> None:
    backend = _Backend(fast=True)
    page = _running_page(qapp, backend)
    backend.sequence = 5
    page._pump_frame()
    assert backend.frame_pulls == 1
    for _ in range(50):
        page._pump_frame()  # nothing new: only the cheap check runs
    assert backend.frame_pulls == 1
    assert backend.sequence_checks == 51
    backend.sequence = 6
    page._pump_frame()
    assert backend.frame_pulls == 2
    page._set_running(False)


def test_the_newest_frame_is_shown(qapp, saved) -> None:
    backend = _Backend(fast=True)
    page = _running_page(qapp, backend)
    backend.sequence = 3
    page._pump_frame()
    assert page._last_sequence == 3
    assert page.preview.has_frame()
    page._set_running(False)


def test_frames_are_not_pulled_while_the_tab_is_hidden(qapp, saved) -> None:
    import time

    backend = _Backend(fast=True)
    page = _running_page(qapp, backend)
    page.hide()
    qapp.processEvents()
    assert not page._frame_timer.isActive()
    backend.sequence = 9
    end = time.perf_counter() + 0.1  # several frame intervals pass while hidden
    while time.perf_counter() < end:
        qapp.processEvents()
    assert backend.frame_pulls == 0
    assert backend.sequence_checks == 0
    page.show()
    qapp.processEvents()
    assert page._frame_timer.isActive()
    page._pump_frame()
    assert page._last_sequence == 9
    page._set_running(False)


def test_the_frame_timer_runs_only_while_capturing(qapp, saved) -> None:
    from fps_coach.ui.pages_setup import FRAME_INTERVAL_MS

    backend = _Backend(fast=True)
    page = _running_page(qapp, backend)
    assert page._frame_timer.isActive()
    assert page._frame_timer.interval() == FRAME_INTERVAL_MS
    page._set_running(False)
    assert not page._frame_timer.isActive()
    assert not page.preview.has_frame()
    assert page._last_sequence == -1


def test_an_old_native_build_keeps_the_slow_poll(qapp, saved) -> None:
    backend = _Backend(fast=False)
    page = _running_page(qapp, backend)
    assert page._frame_timer.interval() == 250
    backend.sequence = 4
    page._pump_frame()
    assert backend.sequence_checks == 0  # the cheap check is not available
    assert page._last_sequence == 4
    page._set_running(False)


def test_stats_refresh_does_not_pull_frames(qapp, saved) -> None:
    backend = _Backend(fast=True)
    page = _running_page(qapp, backend)
    backend.sequence = 7
    page._refresh_stats()
    assert backend.frame_pulls == 0
    page._set_running(False)


def test_a_frame_with_the_same_number_is_not_shown_twice(qapp, saved) -> None:
    backend = _Backend(fast=False)
    page = _running_page(qapp, backend)
    backend.sequence = 2
    page._pump_frame()
    page._pump_frame()
    assert backend.frame_pulls == 2  # no cheap check on an old build, so both polls pull
    assert page._last_sequence == 2
    page._set_running(False)


# -- the backend wrapper ---------------------------------------------------------

def test_core_backend_reports_no_cheap_sequence_without_the_native_core(saved) -> None:
    from fps_coach.backend import CoreBackend

    backend = CoreBackend()
    backend.capture = None
    assert backend.fast_frame_sequence is False
    assert backend.latest_sequence() is None


def test_core_backend_reads_the_sequence_when_the_native_core_has_it(saved) -> None:
    from fps_coach.backend import CoreBackend

    class NativeCapture:
        def latest_sequence(self):
            return 42

    backend = CoreBackend()
    backend.capture = NativeCapture()
    assert backend.fast_frame_sequence is True
    assert backend.latest_sequence() == 42


def test_core_backend_falls_back_when_the_native_core_is_older(saved) -> None:
    from fps_coach.backend import CoreBackend

    backend = CoreBackend()
    backend.capture = object()  # no latest_sequence method
    assert backend.fast_frame_sequence is False
    assert backend.latest_sequence() is None
