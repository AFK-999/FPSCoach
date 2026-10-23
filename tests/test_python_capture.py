"""Live capture without the native core (rev78).

The Capture tab's Source card and live preview used to need the C++ extension, so
on a ``FPSCOACH_SKIP_NATIVE`` install the page showed "No display output found",
a disabled Start button and "No frame yet" — the user saw a Recording card and
nothing else. ``python_capture.PythonDesktopCapture`` is the mss-backed stand-in.

No test here touches a display: every one injects a fake ``sct`` factory. The
fake mirrors the two methods the module uses (``monitors``, ``grab``) plus
``close``, and records the regions it was asked for.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fps_coach import python_capture
from fps_coach.backend import CoreBackend
from fps_coach.python_capture import (
    CAPTURE_NOTICE,
    MAX_FPS,
    MISSING_MSS_MESSAGE,
    PythonDesktopCapture,
    plan_region,
)

MONITOR_1080 = {"left": 0, "top": 0, "width": 1920, "height": 1080}
MONITOR_OFFSET = {"left": 1920, "top": 0, "width": 1280, "height": 720}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved(monkeypatch, tmp_path):
    """Capture-tab settings must not leak into the developer's real QSettings."""
    from fps_coach.ui import pages_setup
    from PySide6.QtCore import QSettings

    store = QSettings(str(tmp_path / "capture.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_setup, "_settings", lambda: store)
    return store


class FakeShot:
    def __init__(self, width: int, height: int) -> None:
        self.size = (width, height)
        self.bgra = bytes([7, 8, 9, 255]) * (width * height)


class FakeSct:
    """Minimal mss double. Takes the *physical* monitors; mss puts the virtual
    bounding box at index 0 and the module relies on that, so build it here."""

    def __init__(self, physical, *, fail_after: int | None = None, delay: float = 0.0) -> None:
        self.monitors = ([_virtual(physical)] if physical else []) + list(physical)
        self.regions: list[dict[str, int]] = []
        self.closed = False
        self._fail_after = fail_after
        self._delay = delay
        self._grabs = 0

    def grab(self, region):
        self.regions.append(dict(region))
        if self._delay:
            time.sleep(self._delay)
        self._grabs += 1
        if self._fail_after is not None and self._grabs > self._fail_after:
            raise OSError("grab failed")
        return FakeShot(region["width"], region["height"])

    def close(self) -> None:
        self.closed = True


def _virtual(physical) -> dict:
    left = min(m["left"] for m in physical)
    top = min(m["top"] for m in physical)
    right = max(m["left"] + m["width"] for m in physical)
    bottom = max(m["top"] + m["height"] for m in physical)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def make_factory(handles: list[FakeSct]):
    """A factory that hands out the same fakes and remembers them."""
    created: list[FakeSct] = []

    def factory() -> FakeSct:
        handle = handles[0] if len(handles) == 1 else handles.pop(0)
        created.append(handle)
        return handle

    factory.created = created  # type: ignore[attr-defined]
    return factory


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- dependency guard ------------------------------------------------------


def test_available_follows_the_guarded_import(monkeypatch):
    monkeypatch.setattr(python_capture, "_mss", None)
    assert python_capture.available() is False

    class Stub:
        pass

    monkeypatch.setattr(python_capture, "_mss", Stub())
    assert python_capture.available() is True


def test_start_without_mss_explains_what_to_install(monkeypatch):
    monkeypatch.setattr(python_capture, "_mss", None)
    capture = PythonDesktopCapture(sct_factory=make_factory([FakeSct([MONITOR_1080])]))
    # A factory was injected, so the guard does not apply ...
    capture.start(output_index=0, width=640, height=360, target_fps=30)
    capture.stop()

    # ... but the default factory is what actually needs mss.
    default = PythonDesktopCapture()
    with pytest.raises(RuntimeError) as excinfo:
        default.start(output_index=0, width=640, height=360, target_fps=30)
    assert MISSING_MSS_MESSAGE in str(excinfo.value)
    assert "pip install mss" in str(excinfo.value)


# --- display enumeration ---------------------------------------------------


def test_enumerate_outputs_maps_every_physical_monitor():
    fake = FakeSct([MONITOR_1080, MONITOR_OFFSET])
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    outputs = capture.enumerate_outputs()
    assert [item["output_index"] for item in outputs] == [0, 1]
    assert [item["width"] for item in outputs] == [1920, 1280]
    assert outputs[1]["left"] == 1920
    assert outputs[0]["adapter_index"] == 0
    assert outputs[0]["adapter_name"] == "Python (mss)"
    assert outputs[0]["attached_to_desktop"] is True
    assert fake.closed is True  # the enumeration handle is closed again


# --- region planning -------------------------------------------------------


def test_plan_region_centers_preset_regions():
    region, mode = plan_region(MONITOR_1080, -1, -1, 640, 360)
    assert region == {"left": 640, "top": 360, "width": 640, "height": 360}
    assert mode == "region"


def test_plan_region_full_output_keeps_the_monitor_origin():
    region, mode = plan_region(MONITOR_OFFSET, -1, -1, 0, 0)
    assert region == {"left": 1920, "top": 0, "width": 1280, "height": 720}
    assert mode == "output"


def test_plan_region_clamps_a_window_that_overhangs_the_monitor():
    # A window partly off-screen: the grab must stay inside the monitor.
    region, mode = plan_region(MONITOR_1080, 1800, 1000, 800, 600)
    assert region["left"] + region["width"] <= 1920
    assert region["top"] + region["height"] <= 1080
    assert mode == "region"


def test_plan_region_rejects_a_degenerate_monitor():
    with pytest.raises(ValueError):
        plan_region({"left": 0, "top": 0, "width": 0, "height": 0}, -1, -1, 640, 360)


# --- the worker ------------------------------------------------------------


def test_start_publishes_frames_with_native_shaped_fields():
    fake = FakeSct([MONITOR_1080])
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    capture.start(output_index=0, width=640, height=360, target_fps=30)
    try:
        assert wait_for(lambda: capture.latest_sequence() >= 2)
        frame = capture.latest_frame()
        assert frame is not None
        assert frame["sequence"] >= 2
        assert (frame["width"], frame["height"]) == (640, 360)
        assert frame["stride"] == 640 * 4
        assert len(frame["bgra"]) == 640 * 360 * 4
        assert frame["pointer_visible"] is False
        # Preset regions are centered, and the grab always asks for that region.
        assert fake.regions[0] == {"left": 640, "top": 360, "width": 640, "height": 360}
    finally:
        capture.stop()
    stats = capture.stats()
    assert stats["running"] is False
    assert stats["frames_captured"] >= 2
    assert stats["capture_width"] == 640
    assert stats["capture_mode"] == "region"


def test_stats_and_latest_sequence_shape_match_the_native_object():
    fake = FakeSct([MONITOR_1080])
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    capture.start(output_index=0, width=0, height=0, target_fps=30)
    try:
        assert wait_for(lambda: capture.latest_sequence() >= 1)
    finally:
        capture.stop()
    stats = capture.stats()
    for key in (
        "running",
        "capture_fps",
        "frames_captured",
        "acquire_timeouts",
        "access_lost_count",
        "last_frame_age_ms",
        "capture_width",
        "capture_height",
        "adapter_name",
        "last_error",
        "capture_mode",
        "notice",
    ):
        assert key in stats, key
    assert stats["capture_mode"] == "output"
    assert stats["capture_width"] == 1920
    assert stats["adapter_name"] == "Python (mss)"


def test_target_fps_is_capped_and_reported():
    fake = FakeSct([MONITOR_1080])
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    capture.start(output_index=0, width=640, height=360, target_fps=120)
    try:
        assert capture.stats()["notice"].startswith(f"Requested 120 FPS; Python capture is capped at {MAX_FPS} FPS.")
    finally:
        capture.stop()


def test_default_notice_names_the_two_limits():
    fake = FakeSct([MONITOR_1080])
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    capture.start(output_index=0, width=640, height=360, target_fps=30)
    try:
        assert capture.stats()["notice"] == CAPTURE_NOTICE
    finally:
        capture.stop()
    assert "60 FPS" in CAPTURE_NOTICE and "behind the window" in CAPTURE_NOTICE


def test_repeated_grab_failures_stop_the_capture_with_an_error():
    fake = FakeSct([MONITOR_1080], fail_after=0)  # every grab raises
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    capture.start(output_index=0, width=640, height=360, target_fps=30)
    assert wait_for(lambda: not capture.running, timeout=5.0)
    stats = capture.stats()
    assert stats["running"] is False
    assert "screen grab failed" in stats["last_error"]


def test_second_start_while_running_is_refused():
    fake = FakeSct([MONITOR_1080])
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    capture.start(output_index=0, width=320, height=180, target_fps=30)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            capture.start(output_index=0, width=320, height=180, target_fps=30)
    finally:
        capture.stop()


def test_stop_is_idempotent_and_closes_the_handle():
    fake = FakeSct([MONITOR_1080])
    capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    capture.start(output_index=0, width=320, height=180, target_fps=30)
    assert wait_for(lambda: capture.latest_sequence() >= 1)
    capture.stop()
    capture.stop()
    assert capture.running is False
    assert fake.closed is True


def test_missing_monitor_is_reported_in_the_calling_thread():
    capture = PythonDesktopCapture(sct_factory=make_factory([FakeSct([])]))
    with pytest.raises(RuntimeError, match="no monitor"):
        capture.start(output_index=0, width=640, height=360, target_fps=30)


# --- backend wiring --------------------------------------------------------


def test_backend_uses_python_capture_when_the_native_core_is_absent(monkeypatch):
    monkeypatch.setattr("fps_coach.backend._native", None)
    monkeypatch.setattr(python_capture, "available", lambda: True)
    backend = CoreBackend()
    assert backend.native_available is False
    assert backend.uses_python_capture is True
    assert backend.core_label == "Python capture (mss)"
    assert backend.fast_frame_sequence is True  # the cheap sequence check works here too


def test_backend_falls_back_to_no_capture_when_mss_is_missing(monkeypatch):
    monkeypatch.setattr("fps_coach.backend._native", None)
    monkeypatch.setattr(python_capture, "available", lambda: False)
    backend = CoreBackend()
    assert backend.capture is None
    assert backend.uses_python_capture is False
    assert backend.core_label == "Python preview mode"
    assert backend.outputs() == []
    stats = backend.capture_stats()
    assert stats["running"] is False
    assert "pip install -e" in stats["last_error"]


def test_backend_prefers_the_native_capture(monkeypatch):
    class FakeNativeCapture:
        def enumerate_outputs(self):
            return []

    class FakeNative:
        DesktopCapture = FakeNativeCapture

        @staticmethod
        def MovementAnalyzer():
            return object()

    monkeypatch.setattr("fps_coach.backend._native", FakeNative)
    backend = CoreBackend()
    assert backend.native_available is True
    assert backend.uses_python_capture is False
    assert backend.core_label == "Native C++ core"


# --- the Capture tab -------------------------------------------------------


def test_capture_page_lists_outputs_and_enables_start(qapp, saved, monkeypatch):
    """The page the user was looking at: with the Python path it is usable."""
    monkeypatch.setattr("fps_coach.backend._native", None)
    monkeypatch.setattr(python_capture, "available", lambda: True)
    from fps_coach.ui.pages_setup import CapturePage

    backend = CoreBackend()
    fake = FakeSct([MONITOR_1080])
    backend.capture = PythonDesktopCapture(sct_factory=make_factory([fake]))
    page = CapturePage(backend)
    try:
        assert page.output_combo.currentData() is not None
        assert "1920" in page.output_combo.currentText()
        assert page.start_btn.isEnabled() is True
        assert page.hwaccel.isEnabled() is False  # no D3D11 without the native core
        assert page.live_source_note.isHidden() is False
        assert "mss" in page.live_source_note.text()
    finally:
        page.shutdown()


def test_capture_page_explains_a_missing_capture_backend(qapp, saved, monkeypatch):
    monkeypatch.setattr("fps_coach.backend._native", None)
    monkeypatch.setattr(python_capture, "available", lambda: False)
    from fps_coach.ui.pages_setup import CapturePage

    backend = CoreBackend()
    page = CapturePage(backend)
    try:
        assert page.output_combo.currentText() == "No display output found"
        assert page.start_btn.isEnabled() is False
        assert page.live_source_note.isHidden() is False
        assert "pip install -e" in page.live_source_note.text()
    finally:
        page.shutdown()
