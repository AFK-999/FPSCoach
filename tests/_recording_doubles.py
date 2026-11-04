"""Shared test doubles for the screen-recorder tests.

A synthetic screen (``FakeMSS``) and a fake encoder (``FakeWriter``) replace
``mss`` and ``cv2.VideoWriter`` so the engine and the Capture-tab wiring can be
tested without a display, a GPU or codec support.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeScreenshot:
    """Duck-typed equivalent of ``mss.screenshot.ScreenShot`` (BGRA)."""

    def __init__(self, width: int, height: int, value: int) -> None:
        pixels = np.empty((height, width, 4), dtype=np.uint8)
        pixels[:, :, 0] = value % 256  # B
        pixels[:, :, 1] = 0  # G
        pixels[:, :, 2] = 0  # R
        pixels[:, :, 3] = 255  # A
        self._pixels = pixels
        self.width = width
        self.height = height

    def __array__(self, dtype=None, copy=None):
        return self._pixels if dtype is None else self._pixels.astype(dtype)


class FakeMSS:
    """Minimal ``mss.mss`` replacement; timing knobs live on the instance."""

    def __init__(self, grab_delay: float = 0.0, fail_after: int | None = None) -> None:
        self.counter = 0
        self.grabs = 0
        self.closed = False
        self.close_calls = 0
        self.grab_delay = grab_delay
        self.fail_after = fail_after
        self.monitors = [
            {"left": 0, "top": 0, "width": 640, "height": 480},
            {"left": 0, "top": 0, "width": 641, "height": 481},  # odd on purpose
        ]

    def grab(self, monitor: dict) -> FakeScreenshot:
        if self.grab_delay:
            time.sleep(self.grab_delay)
        self.counter += 1
        self.grabs += 1
        if self.fail_after is not None and self.grabs > self.fail_after:
            raise OSError("simulated display disconnect")
        return FakeScreenshot(monitor["width"], monitor["height"], self.counter)

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1


class FakeWriter:
    """Records what the capture thread writes; mimics ``cv2.VideoWriter``."""

    def __init__(self, path, fourcc, fps, size, opened: bool = True) -> None:
        self.path = Path(path)
        self.fourcc = fourcc
        self.fps = fps
        self.size = size
        self.opened = opened
        self.frames: list[np.ndarray] = []
        self.released = False
        self.release_calls = 0
        self.write_thread: threading.Thread | None = None
        self.release_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def write(self, frame: np.ndarray) -> None:
        with self._lock:
            self.frames.append(np.array(frame, copy=True))
        self.write_thread = threading.current_thread()
        self.path.write_bytes(b"fake video")  # the file really exists

    def isOpened(self) -> bool:
        return self.opened

    def release(self) -> None:
        self.released = True
        self.release_calls += 1
        self.release_thread = threading.current_thread()


class Harness:
    """Injected fixtures for one recorder."""

    def __init__(self, opened: bool = True, **mss_kwargs) -> None:
        self.writers: list[FakeWriter] = []
        self.screens: list[FakeMSS] = []
        self.opened = opened
        self.mss_kwargs = mss_kwargs

    def sct_factory(self) -> FakeMSS:
        screen = FakeMSS(**self.mss_kwargs)
        self.screens.append(screen)
        return screen

    def writer_factory(self, path, fourcc, fps, size) -> FakeWriter:
        writer = FakeWriter(path, fourcc, fps, size, opened=self.opened)
        self.writers.append(writer)
        return writer

    @property
    def writer(self) -> FakeWriter:
        assert self.writers, "no writer was created"
        return self.writers[-1]

    @property
    def screen(self) -> FakeMSS:
        assert self.screens, "no mss instance was created"
        return self.screens[-1]
