"""Desktop-to-screen latency through the real CapturePage, CoreBackend and native worker.

The fake desktop records when each frame appeared. For every UI pass we work out which desktop
frame is on screen (the frame's pixel carries its number modulo 256) and measure how long ago it
appeared. This is the delay a user sees between a change on the desktop and the preview showing it
(excluding the monitor's own scan-out).

usage (from the repo root, after building build_fake, see README.txt):
  PYTHONPATH=python:build_fake QT_QPA_PLATFORM=offscreen python3 native/tests/fake_dxgi/bench_desktop_age.py 6 balanced 60
PRESET is quality, balanced or low. Requires the fake desktop build; it cannot run on Windows.
"""
import ctypes
import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
PRESET = sys.argv[2] if len(sys.argv) > 2 else "balanced"
FPS = int(sys.argv[3]) if len(sys.argv) > 3 else 60

from fps_coach.backend import CoreBackend  # noqa: E402
from fps_coach.ui.pages_setup import CapturePage  # noqa: E402
import fpscoach_native  # noqa: E402

lib = ctypes.CDLL(fpscoach_native.__file__)
lib.fake_desktop_frame_count.restype = ctypes.c_uint64
lib.fake_desktop_frame_time_us.restype = ctypes.c_uint64
lib.fake_desktop_frame_time_us.argtypes = [ctypes.c_uint64]


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))]


def main():
    backend = CoreBackend()
    page = CapturePage(backend)
    page.resize(1280, 800)
    page.show()
    app.processEvents()
    page.preset.setCurrentIndex(page.preset.findData(PRESET))
    page.fps_combo.setCurrentIndex(page.fps_combo.findData(FPS))

    pull_cost = []
    pulls = [0]
    shown_desktop_frame = [None]
    original = backend.latest_frame

    def timed_latest_frame():
        t0 = time.perf_counter()
        frame = original()
        pull_cost.append((time.perf_counter() - t0) * 1000)
        if frame:
            pulls[0] += 1
            count = lib.fake_desktop_frame_count()
            r = frame["bgra"][(7 * frame["stride"]) + 5 * 4 + 2]
            # The newest desktop frame whose number ends in the pixel's byte.
            shown_desktop_frame[0] = count - ((count - r) & 255)
        return frame

    backend.latest_frame = timed_latest_frame

    page._start()
    ages = []
    loop_costs = []
    end = time.perf_counter() + SECONDS
    while time.perf_counter() < end:
        t0 = time.perf_counter()
        app.processEvents()
        loop_costs.append((time.perf_counter() - t0) * 1000)
        n = shown_desktop_frame[0]
        if n is not None and n > 0:
            appeared = lib.fake_desktop_frame_time_us(n)
            ages.append((time.monotonic_ns() // 1000 - appeared) / 1000.0)
        time.sleep(0.001)
    stats = backend.capture_stats()
    page._stop()

    print(f"== {PRESET} at {FPS} FPS, {SECONDS:.0f} s")
    print(f"  capture: {stats.get('capture_fps', 0):.1f} FPS; preview updates {pulls[0] / SECONDS:.1f}/s")
    if pull_cost:
        print(f"  UI pull cost: median {statistics.median(pull_cost):.2f} ms, p95 {pct(pull_cost, 0.95):.2f} ms")
    if ages:
        print(f"  desktop change to screen: median {statistics.median(ages):.1f} ms, "
              f"p95 {pct(ages, 0.95):.1f} ms, max {max(ages):.1f} ms")
    print(f"  UI loop passes over 8 ms: {sum(c > 8 for c in loop_costs)} of {len(loop_costs)}, "
          f"worst {max(loop_costs):.1f} ms")


if __name__ == "__main__":
    main()
