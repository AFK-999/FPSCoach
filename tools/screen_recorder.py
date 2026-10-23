#!/usr/bin/env python3
"""
screen_recorder.py -- FPS Coach developer tool (rev72)
=====================================================

Background screen-capture utility built on ``mss``, ``numpy`` and
``threading``, used to prototype and test the capture/encode path outside the
Qt application.  It records the primary monitor into a temporary video file in
the background while keeping an always-current copy of the latest frame
available to the rest of the process.  A second background worker ("analysis
timer") fires a callback at a fixed, configurable interval so that expensive
processing (a mock analyser, a preview, a codec check, ...) can run
concurrently with recording without ever blocking the capture loop.

**Where this sits in the project.**  This file lives in ``tools/`` and is not
imported by ``fps_coach``: the shipped application keeps using the native
D3D11/DXGI capture path in ``native/``.  It is a diagnostics/utility module for
developers (codec checks, timing experiments, synthetic frames in tests), and
deliberately contains no game analysis.  The fair-play boundary in
``docs/PRIVACY.md`` and ``docs/architecture.md`` is unchanged: nothing here is
wired into the UI, and the product still does not record or analyse gameplay.

Design overview
---------------
1. **Real-time background frame buffering**
   The capture thread writes every grabbed frame to the video file *and*
   publishes it into a :class:`LatestFrameBuffer`.  The buffer is a
   single-slot, lock-protected, sequence-numbered mailbox: readers always get
   a private copy of the most recent frame and can never see a half-written
   array, and writers never block on slow consumers.

2. **Concurrent analysis timer loop**
   A dedicated daemon thread (``screen-analysis``) wakes up every
   ``analysis_interval`` seconds (drift-compensated, shut-down-aware via
   ``threading.Event.wait``) and invokes
   ``analysis_callback(latest_frame_copy, sequence)``.  A slow or crashing
   callback never stalls recording: callback exceptions are logged and the
   timer keeps ticking.  Missed ticks (for example while the machine slept)
   are skipped rather than replayed in a burst.

3. **Resource safety and cleanup**
   ``ScreenRecorder`` is a context manager.  ``stop()`` sets a shared stop
   event, joins both workers (bounded by ``join_timeout``) and lets the capture
   thread release the ``cv2.VideoWriter`` and close its own ``mss`` handle on
   the thread that opened them (``mss`` handles are not thread-safe and are
   created per-thread).  ``cleanup()`` additionally deletes the temporary file,
   clears the frame buffer and is idempotent.  ``__exit__`` always calls
   ``cleanup()``, even if the ``with`` body raised.  An ``atexit`` hook is
   registered while recording, so an unexpected interpreter shutdown still
   removes the temporary file.

Requirements
------------
``pip install -e ".[recorder]"`` pulls ``mss``, ``numpy`` and
``opencv-python-headless`` (the ``recorder`` extra in ``pyproject.toml``).
``cv2`` is used purely as the video encoder; both ``mss`` and ``cv2`` are
imported defensively so the module can be imported (and tested) on machines
where one of them is missing, and callers can inject test doubles through
``sct_factory`` / ``writer_factory``.

Usage
-----
As a library::

    from screen_recorder import ScreenRecorder, mock_analysis_callback

    with ScreenRecorder(fps=15, analysis_interval=2.0,
                        analysis_callback=mock_analysis_callback) as rec:
        frame = rec.latest_frame          # numpy array (BGR) or None
        ...                               # callback fires every 2 s

    # On leaving the block: threads joined, temporary video deleted.

From the command line::

    python tools/screen_recorder.py --duration 10 --analysis-interval 1.5
    python tools/screen_recorder.py --keep --output-dir . --log-level DEBUG

Notes
-----
* ``monitor_index=1`` is the primary monitor in ``mss``; ``0`` is the union of
  all monitors and ``2+`` are secondary monitors.
* Width and height are rounded down to even numbers because several codecs
  (including ``mp4v``) reject odd frame dimensions.
* mp4v + ``.mp4`` is the default; if ``cv2.VideoWriter.isOpened()`` fails, the
  capture thread reports a :class:`RuntimeError` (visible via
  ``recorder.capture_error``) suggesting a codec/FFmpeg problem.
"""


from __future__ import annotations

import argparse
import atexit
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Protocol

import numpy as np

try:  # pragma: no cover - exercised implicitly by the import guards
    import cv2
except Exception:  # pragma: no cover - OpenCV is optional at import time
    cv2 = None  # type: ignore[assignment]

try:  # pragma: no cover
    import mss
except Exception:  # pragma: no cover - mss is optional at import time
    mss = None  # type: ignore[assignment]

__all__ = [
    "LatestFrameBuffer",
    "ScreenRecorder",
    "mock_analysis_callback",
    "VideoWriterLike",
]

LOGGER = logging.getLogger("fpscoach.screen_recorder")

#: Callback invoked by the analysis thread.
#: Receives a private copy of the newest BGR frame and its monotonically
#: increasing buffer sequence number.
AnalysisCallback = Callable[[np.ndarray, int], None]

#: Factory returning an ``mss``-compatible screen-capture object.
SctFactory = Callable[[], object]

PathLike = str | os.PathLike[str]


class VideoWriterLike(Protocol):
    """Structural type for the object returned by ``writer_factory``."""

    def write(self, frame: np.ndarray) -> None:  # pragma: no cover - protocol
        ...

    def release(self) -> None:  # pragma: no cover - protocol
        ...

    def isOpened(self) -> bool:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Thread-safe latest-frame buffer
# ---------------------------------------------------------------------------
class LatestFrameBuffer:
    """Single-slot, thread-safe mailbox holding the most recent frame.

    The capture thread :meth:`publish`es frames; any number of readers call
    :meth:`snapshot` / :attr:`latest` at their own pace.  The slot only ever
    holds one frame (old frames are overwritten), so memory usage is bounded
    even when the consumer is much slower than the producer.

    Every read returns an independent copy.  This guarantees that a consumer
    cannot mutate the frame the writer is still using and that no reader can
    observe a partially written array.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._sequence = 0
        self._has_frame = threading.Event()

    def publish(self, frame: np.ndarray, *, copy: bool = True) -> int:
        """Store ``frame`` as the latest frame and return its new sequence id.

        ``copy=False`` may be used when the caller owns ``frame`` exclusively
        and will never mutate it again (as is the case for a freshly decoded
        frame in the capture loop); it avoids one full-frame copy per tick.
        """
        stored = np.array(frame, copy=True) if copy else frame
        with self._lock:
            self._frame = stored
            self._sequence += 1
            sequence = self._sequence
        self._has_frame.set()
        return sequence

    def snapshot(self) -> tuple[np.ndarray | None, int]:
        """Return ``(copy_of_latest_frame, sequence)`` or ``(None, 0)``."""
        with self._lock:
            if self._frame is None:
                return None, 0
            return self._frame.copy(), self._sequence

    @property
    def latest(self) -> np.ndarray | None:
        """A private copy of the newest frame, or ``None`` if none yet."""
        return self.snapshot()[0]

    @property
    def sequence(self) -> int:
        """Sequence number of the newest published frame (0 = nothing yet)."""
        with self._lock:
            return self._sequence

    def wait_for_frame(self, timeout: float | None = None) -> bool:
        """Block until at least one frame has been published (or timeout)."""
        return self._has_frame.wait(timeout)

    def clear(self) -> None:
        """Drop the stored frame (called by :meth:`ScreenRecorder.cleanup`)."""
        with self._lock:
            self._frame = None
            self._sequence = 0
            self._has_frame.clear()

    def __len__(self) -> int:
        return 1 if self._has_frame.is_set() else 0


# ---------------------------------------------------------------------------
# Default callback / encoder helpers
# ---------------------------------------------------------------------------
def mock_analysis_callback(frame: np.ndarray, sequence: int) -> None:
    """Placeholder for real analysis (OCR, detection, motion, ...).

    Runs on the analysis thread every ``analysis_interval`` seconds.  It only
    reads cheap statistics so it doubles as a health check that the buffer is
    receiving frames.  Replace it with your own callable of the same shape.
    """
    height, width = frame.shape[:2]
    LOGGER.info(
        "mock analysis: sequence=%d frame=%dx%d dtype=%s mean=%.1f",
        sequence,
        width,
        height,
        frame.dtype,
        float(frame.mean()),
    )


def _default_sct_factory() -> object:
    """Create an ``mss`` instance.  Must be called on the thread using it."""
    if mss is None:  # pragma: no cover - guarded by ScreenRecorder.start()
        raise RuntimeError(
            "The 'mss' package is required for screen capture. "
            "Install it with: pip install mss"
        )
    # mss >= 10 exposes mss.MSS and deprecates the mss.mss() factory; older
    # releases (6.x-9.x) only provide the factory.
    factory = getattr(mss, "MSS", None)
    if factory is not None:
        return factory()
    return mss.mss()  # type: ignore[attr-defined]


def _default_writer_factory(
    path: Path, fourcc: str, fps: float, size: tuple[int, int]
) -> VideoWriterLike:
    """Open an OpenCV ``VideoWriter`` for ``path`` at ``size`` (w, h)."""
    if cv2 is None:  # pragma: no cover - guarded by ScreenRecorder.start()
        raise RuntimeError(
            "OpenCV is required to encode the capture. "
            "Install it with: pip install opencv-python"
        )
    if len(fourcc) != 4:
        raise ValueError(f"fourcc must be exactly 4 characters, got {fourcc!r}")
    code = cv2.VideoWriter_fourcc(*fourcc)
    return cv2.VideoWriter(str(path), code, float(fps), (int(size[0]), int(size[1])))


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------
class ScreenRecorder:
    """Background screen recorder with latest-frame buffering and an analysis timer.

    Parameters
    ----------
    fps:
        Target capture rate for the video file and the frame buffer.
    analysis_interval:
        Seconds between analysis callback invocations (must be > 0).
    monitor_index:
        ``mss`` monitor index.  ``1`` = primary monitor (default), ``0`` = the
        union of all monitors, ``2+`` = secondary monitors.
    analysis_callback:
        Callable ``(frame_bgr, sequence) -> None`` executed on the analysis
        thread.  Defaults to :func:`mock_analysis_callback`.  Exceptions raised
        by the callback are logged and swallowed so the timer survives.
    output_dir:
        Directory for the temporary video file.  Defaults to the system temp
        directory.  Created if it does not exist.
    fourcc:
        Four-character codec code for the video writer (default ``"mp4v"``).
    video_suffix:
        Extension for the temporary video file (default ``".mp4"``).
    keep_temp_file:
        If ``True``, :meth:`cleanup` leaves the recording on disk instead of
        deleting it (the path is available via :attr:`video_path`).
    join_timeout:
        Maximum seconds to wait for each worker when stopping.
    sct_factory / writer_factory:
        Injection seams for tests or custom encoders.  Defaults to ``mss.mss``
        and ``cv2.VideoWriter``.

    The instance is a context manager; ``__exit__`` always calls
    :meth:`cleanup`, which stops both threads and removes the temporary file.
    """

    def __init__(
        self,
        *,
        fps: float = 15.0,
        analysis_interval: float = 1.0,
        monitor_index: int = 1,
        analysis_callback: AnalysisCallback | None = None,
        output_dir: PathLike | None = None,
        fourcc: str = "mp4v",
        video_suffix: str = ".mp4",
        keep_temp_file: bool = False,
        join_timeout: float = 5.0,
        sct_factory: SctFactory | None = None,
        writer_factory: (
            Callable[[Path, str, float, tuple[int, int]], VideoWriterLike] | None
        ) = None,
    ) -> None:
        if fps <= 0:
            raise ValueError(f"fps must be > 0, got {fps!r}")
        if analysis_interval <= 0:
            raise ValueError(
                f"analysis_interval must be > 0, got {analysis_interval!r}"
            )
        if join_timeout <= 0:
            raise ValueError(f"join_timeout must be > 0, got {join_timeout!r}")
        if not video_suffix.startswith("."):
            raise ValueError("video_suffix must start with '.', e.g. '.mp4'")

        self.fps = float(fps)
        self.analysis_interval = float(analysis_interval)
        self.monitor_index = int(monitor_index)
        self.analysis_callback: AnalysisCallback = (
            analysis_callback or mock_analysis_callback
        )
        self.fourcc = fourcc
        self.video_suffix = video_suffix
        self.keep_temp_file = bool(keep_temp_file)
        self.join_timeout = float(join_timeout)

        self._output_dir = (
            Path(output_dir).expanduser().resolve() if output_dir is not None else None
        )
        self._sct_factory = sct_factory
        self._writer_factory = writer_factory

        # Shared state protected by locks.
        self._buffer = LatestFrameBuffer()
        self._lifecycle_lock = threading.RLock()
        self._stats_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._capture_thread: threading.Thread | None = None
        self._analysis_thread: threading.Thread | None = None

        self._temp_path: Path | None = None
        self._started = False
        self._cleaned = False
        self._atexit_registered = False

        self._frames_written = 0
        self._analysis_invocations = 0
        self._capture_error: BaseException | None = None
        self._analysis_error: BaseException | None = None
        self._capture_finished = threading.Event()
        self._analysis_finished = threading.Event()

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> ScreenRecorder:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # Never suppress an exception raised inside the ``with`` body.
        self.cleanup()
        return False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> ScreenRecorder:
        """Create the temp file and launch the capture + analysis threads."""
        with self._lifecycle_lock:
            if self._cleaned:
                raise RuntimeError("recorder has been cleaned up and cannot restart")
            if self._started:
                raise RuntimeError("recorder is already running")

            self._require_dependencies()
            self._temp_path = self._create_temp_file()
            self._stop_event.clear()
            self._capture_finished.clear()
            self._analysis_finished.clear()
            self._capture_error = None
            self._analysis_error = None

            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="screen-capture",
                daemon=True,
            )
            self._analysis_thread = threading.Thread(
                target=self._analysis_loop,
                name="screen-analysis",
                daemon=True,
            )
            self._threads = [self._capture_thread, self._analysis_thread]

            # Safety net: if the interpreter exits while a recording is alive,
            # atexit runs before daemon threads are forcibly killed and still
            # gets a chance to stop the workers and unlink the temp file.
            if not self._atexit_registered:
                atexit.register(self._atexit_cleanup)
                self._atexit_registered = True

            self._started = True
            try:
                self._capture_thread.start()
                self._analysis_thread.start()
            except BaseException:
                # Never leak the temp file or a half-started worker.
                self._stop_event.set()
                self._started = False
                for thread in self._threads:
                    if thread.is_alive() and thread is not threading.current_thread():
                        thread.join(1.0)
                if self._temp_path is not None:
                    self._remove_temp_file(self._temp_path)
                if self._atexit_registered:
                    atexit.unregister(self._atexit_cleanup)
                    self._atexit_registered = False
                self._cleaned = True  # instance is unusable after a failed start
                raise

        LOGGER.info("recording started: %s (fps=%s)", self.video_path, self.fps)
        return self

    def stop(self, *, timeout: float | None = None) -> None:
        """Signal both workers to finish and join them.

        Safe to call multiple times and safe to call from the analysis
        callback (the calling thread is not joined with itself).  The capture
        thread releases the video writer and closes its ``mss`` handle on the
        way out; the temporary file is *not* deleted here -- that is
        :meth:`cleanup`'s job.
        """
        with self._lifecycle_lock:
            self._stop_event.set()
            threads = [t for t in self._threads if t.is_alive()]
            self._started = False

        if not threads:
            return

        deadline = time.monotonic() + (self.join_timeout if timeout is None else timeout)
        for thread in threads:
            if thread is threading.current_thread():
                # stop() was called from inside a worker (e.g. the analysis
                # callback); it will exit on its own once the callback returns.
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)

        # The calling thread legitimately cannot join itself; only warn about
        # workers that are still running after the join deadline.
        current = threading.current_thread()
        stuck = [t.name for t in threads if t.is_alive() and t is not current]
        if stuck:
            LOGGER.warning("threads still alive after stop(): %s", ", ".join(stuck))

    def cleanup(self) -> None:
        """Stop everything, clear the buffer and delete the temp file.

        Idempotent: calling it twice (or via ``__exit__`` and ``atexit``) is
        harmless.  The video file is kept when ``keep_temp_file=True``.
        """
        with self._lifecycle_lock:
            if self._cleaned:
                return

        self.stop()

        with self._lifecycle_lock:
            if self._cleaned:
                return
            self._cleaned = True
            path = self._temp_path
            if self._atexit_registered:
                atexit.unregister(self._atexit_cleanup)
                self._atexit_registered = False
            self._buffer.clear()

        if path is not None:
            if self.keep_temp_file:
                LOGGER.info("keeping recording at %s", path)
            else:
                self._remove_temp_file(path)

        LOGGER.info(
            "cleanup complete: %d frame(s) written, %d analysis call(s)",
            self.frames_written,
            self.analysis_invocations,
        )

    # -- public state ------------------------------------------------------
    @property
    def latest_frame(self) -> np.ndarray | None:
        """Private copy of the most recent frame (BGR ``uint8``) or ``None``."""
        return self._buffer.latest

    @property
    def latest_sequence(self) -> int:
        """Sequence number of the latest buffered frame (0 if none yet)."""
        return self._buffer.sequence

    def wait_for_first_frame(self, timeout: float | None = None) -> bool:
        """Convenience wrapper around :meth:`LatestFrameBuffer.wait_for_frame`."""
        return self._buffer.wait_for_frame(timeout)

    @property
    def frames_written(self) -> int:
        with self._stats_lock:
            return self._frames_written

    @property
    def analysis_invocations(self) -> int:
        with self._stats_lock:
            return self._analysis_invocations

    @property
    def video_path(self) -> Path | None:
        """Temp video path (still exists until :meth:`cleanup` unless kept)."""
        with self._lifecycle_lock:
            return self._temp_path

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._started and not self._stop_event.is_set()

    @property
    def capture_error(self) -> BaseException | None:
        """Exception that terminated the capture thread, if any."""
        return self._capture_error

    @property
    def analysis_error(self) -> BaseException | None:
        """Fatal exception of the analysis thread (callback errors excluded)."""
        return self._analysis_error

    @property
    def recorded_seconds(self) -> float:
        """Approximate recording length derived from frames written."""
        return self.frames_written / self.fps

    # -- internals: setup --------------------------------------------------
    def _require_dependencies(self) -> None:
        if self._sct_factory is None and mss is None:
            raise RuntimeError(
                "The 'mss' package is required for screen capture. "
                "Install it with: pip install mss"
            )
        if self._writer_factory is None and cv2 is None:
            raise RuntimeError(
                "OpenCV is required to encode the capture. "
                "Install it with: pip install opencv-python "
                "(or pass a custom writer_factory)."
            )

    def _create_temp_file(self) -> Path:
        directory: str | None = None
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            directory = str(self._output_dir)
        fd, name = tempfile.mkstemp(
            prefix="bg_capture_", suffix=self.video_suffix, dir=directory
        )
        os.close(fd)
        # An empty file already exists; cv2.VideoWriter truncates/overwrites it.
        return Path(name)

    def _make_sct(self) -> object:
        factory = self._sct_factory or _default_sct_factory
        return factory()

    def _make_writer(
        self, path: Path, fps: float, size: tuple[int, int]
    ) -> VideoWriterLike:
        factory = self._writer_factory or _default_writer_factory
        return factory(path, self.fourcc, fps, size)

    def _select_monitor(self, sct: object) -> dict:
        monitors: Sequence[dict] = getattr(sct, "monitors", None)  # type: ignore[assignment]
        if not monitors:
            raise RuntimeError("no monitors reported by mss; is a display available?")
        if not 0 <= self.monitor_index < len(monitors):
            raise IndexError(
                f"monitor_index {self.monitor_index} out of range; "
                f"available indices: 0..{len(monitors) - 1} "
                "(0 = all monitors, 1 = primary)"
            )
        monitor = dict(monitors[self.monitor_index])
        # Round down to even dimensions: several codecs (mp4v included) refuse
        # odd frame sizes.
        width = int(monitor["width"]) // 2 * 2
        height = int(monitor["height"]) // 2 * 2
        if width <= 0 or height <= 0:
            raise ValueError(
                f"monitor {self.monitor_index} has invalid size "
                f"{monitor['width']}x{monitor['height']}"
            )
        monitor["width"] = width
        monitor["height"] = height
        return monitor

    @staticmethod
    def _to_bgr(shot: object) -> np.ndarray:
        """Normalise an ``mss`` screenshot into a contiguous BGR ``uint8`` array."""
        array = np.asarray(shot, dtype=np.uint8)
        if array.ndim == 2:  # grayscale safety net
            array = np.repeat(array[:, :, None], 3, axis=2)
        if array.ndim != 3:
            raise ValueError(f"unexpected screenshot shape: {array.shape}")
        if array.shape[2] == 4:  # mss default: BGRA
            if cv2 is not None:
                return cv2.cvtColor(array, cv2.COLOR_BGRA2BGR)
            # Fallback without OpenCV: drop the alpha channel (copy because the
            # slice is not contiguous and the writer's lifetime exceeds ours).
            return np.ascontiguousarray(array[:, :, :3])
        if array.shape[2] == 3:
            return np.ascontiguousarray(array)
        raise ValueError(f"unexpected screenshot shape: {array.shape}")

    # -- worker threads ----------------------------------------------------
    def _capture_loop(self) -> None:
        """Continuously grab the monitor, encode it and publish the latest frame."""
        writer: VideoWriterLike | None = None
        sct: object | None = None
        try:
            # mss handles are not thread-safe: create (and close) ours here,
            # on the thread that actually uses it.
            sct = self._make_sct()
            monitor = self._select_monitor(sct)
            size = (monitor["width"], monitor["height"])
            assert self._temp_path is not None
            writer = self._make_writer(self._temp_path, self.fps, size)
            if not writer.isOpened():
                raise RuntimeError(
                    "cv2.VideoWriter failed to open the output file; "
                    f"codec={self.fourcc!r} is probably unavailable in this "
                    "OpenCV/FFmpeg build. Try fourcc='XVID' with "
                    "video_suffix='.avi', or install a full FFmpeg build."
                )

            frame_interval = 1.0 / self.fps
            next_deadline = time.perf_counter()
            grab_failures = 0

            while not self._stop_event.is_set():
                try:
                    shot = sct.grab(monitor)  # type: ignore[attr-defined]
                except Exception:
                    # Tolerate a handful of transient grab errors (e.g. a
                    # display/DPMS hiccup); give up after repeated failures.
                    grab_failures += 1
                    if grab_failures > 5:
                        raise
                    LOGGER.warning(
                        "screen grab failed (%d/5), retrying", grab_failures
                    )
                    self._stop_event.wait(0.05)
                    continue
                grab_failures = 0

                frame = self._to_bgr(shot)

                # 1) encode the frame into the temp video
                writer.write(frame)
                # 2) publish it for readers. copy=False is safe: `frame` is a
                #    fresh array owned by this iteration and never mutated.
                self._buffer.publish(frame, copy=False)

                with self._stats_lock:
                    self._frames_written += 1

                # Drift-free pacing: sleep until the next frame is due, but
                # wake immediately if stop() is requested.
                next_deadline += frame_interval
                remaining = next_deadline - time.perf_counter()
                if remaining > 0:
                    if self._stop_event.wait(remaining):
                        break
                else:
                    # We fell behind (slow encoder); resync instead of trying
                    # to catch up with a burst of frames.
                    next_deadline = time.perf_counter()

        except BaseException as exc:  # noqa: BLE001 - surfaced via property
            self._capture_error = exc
            LOGGER.exception("capture thread stopped by an error: %s", exc)
            self._stop_event.set()  # also wind down the analysis thread
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    LOGGER.exception("failed to release the video writer")
            if sct is not None:
                try:
                    sct.close()  # type: ignore[attr-defined]
                except Exception:
                    LOGGER.exception("failed to close the mss handle")
            self._capture_finished.set()

    def _analysis_loop(self) -> None:
        """Fire ``analysis_callback`` every ``analysis_interval`` seconds.

        Uses ``Event.wait`` for the delay so shutdown is immediate even when
        the interval is long.  Ticks are scheduled on an absolute timeline to
        prevent cumulative drift; if the process falls more than one interval
        behind, missed ticks are skipped rather than fired back-to-back.
        """
        interval = self.analysis_interval
        next_tick = time.perf_counter() + interval
        try:
            while not self._stop_event.is_set():
                remaining = next_tick - time.perf_counter()
                if remaining > 0:
                    if self._stop_event.wait(remaining):
                        break
                if self._stop_event.is_set():
                    break

                frame, sequence = self._buffer.snapshot()
                if frame is not None:
                    with self._stats_lock:
                        self._analysis_invocations += 1
                    try:
                        self.analysis_callback(frame, sequence)
                    except Exception:
                        # A broken callback must never kill the timer or the
                        # recording; log it and keep ticking.
                        LOGGER.exception(
                            "analysis callback raised; timer continues"
                        )
                else:
                    LOGGER.debug("analysis tick skipped: no frame captured yet")

                # Absolute schedule with missed-tick skipping.
                next_tick += interval
                now = time.perf_counter()
                if next_tick <= now:
                    missed = int((now - next_tick) // interval) + 1
                    next_tick += missed * interval
        except BaseException as exc:  # noqa: BLE001 - surfaced via property
            self._analysis_error = exc
            LOGGER.exception("analysis thread stopped by an error: %s", exc)
            self._stop_event.set()
        finally:
            self._analysis_finished.set()

    # -- teardown helpers --------------------------------------------------
    def _remove_temp_file(self, path: Path) -> None:
        """Unlink the temp file, retrying briefly on Windows file locks."""
        for attempt in range(3):
            try:
                path.unlink(missing_ok=True)
                LOGGER.debug("removed temporary video %s", path)
                return
            except OSError as exc:
                if attempt == 2:
                    LOGGER.warning("could not remove %s: %s", path, exc)
                    return
                time.sleep(0.05)

    def _atexit_cleanup(self) -> None:  # pragma: no cover - exit-time path
        try:
            self.cleanup()
        except Exception:
            LOGGER.exception("atexit cleanup failed")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(fps={self.fps!r}, "
            f"analysis_interval={self.analysis_interval!r}, "
            f"monitor_index={self.monitor_index!r}, "
            f"running={self.is_running!r})"
        )


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record the primary monitor in the background, buffer the latest "
            "frame and run a mock analysis callback on a fixed interval."
        )
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="seconds to record; 0 (default) = run until Ctrl+C",
    )
    parser.add_argument("--fps", type=float, default=15.0, help="capture rate")
    parser.add_argument(
        "--analysis-interval",
        type=float,
        default=2.0,
        help="seconds between analysis callback invocations",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="mss monitor index (1 = primary, 0 = all monitors)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="directory for the temporary video (default: system temp)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the recorded video instead of deleting it on exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(threadName)s] %(levelname)s %(message)s",
    )

    recorder = ScreenRecorder(
        fps=args.fps,
        analysis_interval=args.analysis_interval,
        monitor_index=args.monitor,
        analysis_callback=mock_analysis_callback,
        output_dir=args.output_dir,
        keep_temp_file=args.keep,
    )

    interrupted = False
    try:
        with recorder:
            print(f"Recording to {recorder.video_path} (Ctrl+C to stop)")
            deadline = (
                time.monotonic() + args.duration if args.duration > 0 else None
            )
            try:
                while recorder.is_running:
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
            except KeyboardInterrupt:
                interrupted = True
                print("\nStopping...")
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 1

    print(
        f"Done: {recorder.frames_written} frame(s) "
        f"(~{recorder.recorded_seconds:.1f}s), "
        f"{recorder.analysis_invocations} analysis call(s)."
    )
    if recorder.capture_error is not None:
        print(f"Capture error: {recorder.capture_error}")
        return 1
    if recorder.keep_temp_file and recorder.video_path is not None:
        path = recorder.video_path
        if path.exists():
            print(f"Video kept at: {path} ({path.stat().st_size / 1024:.0f} KiB)")
        else:
            print("No video file was produced.")
            return 1
    elif not interrupted:
        print("Temporary video deleted on exit.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
