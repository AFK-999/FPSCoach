"""Real-time screen capture without the native C++ core.

``fps_coach_native.DesktopCapture`` is the production path: Desktop Duplication
on the GPU, which can record an occluded window and runs at 240 FPS. It only
exists in a Windows build with the C++ extension (see ``FPSCOACH_SKIP_NATIVE``
in ``CMakeLists.txt``). On a machine without it the Capture tab used to be inert
— no display outputs, Start disabled, "No frame yet" — which made the live
capture section look missing entirely and left the Recording card as the only
working thing on the page.

``PythonDesktopCapture`` is a drop-in stand-in with the same method names as the
native object, backed by ``mss`` on a background thread. It gives the Capture
tab real-time capture and the live preview from a plain ``pip install``.

Parity with the native object (``CoreBackend`` and ``CapturePage`` only use
these):

    enumerate_outputs()                      -> list of display dicts
    start(adapter, output, left, top, w, h, fps)
    stop()
    running                                  -> bool
    stats()                                  -> dict (same keys as native)
    latest_sequence()                        -> int
    latest_frame()                           -> dict | None (BGRA bytes)

Differences, all reported through ``stats()["notice"]`` rather than hidden:

* capped at ``MAX_FPS`` (60) — a Python grab loop at 120 FPS burns CPU for no
  benefit;
* no single-window capture (mss reads the composited screen), so a window is
  captured as the screen area behind it;
* no hardware cursor overlay, so ``pointer_visible`` is always False.

``mss`` is imported lazily and guarded, exactly like ``screen_recorder``: this
module must import on a machine where the recorder extra is absent.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

try:  # optional: the app must import without the recorder extra (rev76/rev77)
    import mss as _mss
except ImportError:  # pragma: no cover - exercised through the blocked-import tests
    _mss = None

#: A Python grab loop is CPU-bound; past this the preview stops being useful.
MAX_FPS = 60
#: Consecutive grab failures tolerated before the capture is declared broken.
MAX_GRAB_FAILURES = 5
#: Rolling window used for the FPS figure in ``stats()``.
FPS_WINDOW_S = 1.0

CAPTURE_NOTICE = (
    f"Python capture (mss): up to {MAX_FPS} FPS; single-window capture shows the "
    "screen area behind the window."
)
MISSING_MSS_MESSAGE = (
    "Live capture needs the mss package. Install it with: "
    'pip install -e ".[recorder]" (or: pip install mss)'
)


def available() -> bool:
    """True when this machine can capture through ``mss``."""
    return _mss is not None


def _monitors(sct: Any) -> list[dict[str, Any]]:
    monitors = getattr(sct, "monitors", None)
    return [dict(monitor) for monitor in (monitors or [])]


def _shot_bgra(shot: Any) -> bytes:
    """BGRA bytes out of an ``mss`` screenshot (or a test double shaped like one)."""
    data = getattr(shot, "bgra", None)
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    raw = getattr(shot, "raw", None)
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    raise RuntimeError("screenshot object exposes neither .bgra nor .raw bytes")


def _shot_size(shot: Any) -> tuple[int, int]:
    size = getattr(shot, "size", None)
    if size is not None:
        width, height = size
        return int(width), int(height)
    raise RuntimeError("screenshot object exposes no .size")


def plan_region(
    monitor: dict[str, Any], left: int, top: int, width: int, height: int
) -> tuple[dict[str, int], str]:
    """Work out which screen rectangle to grab, and the mode to report.

    ``left``/``top`` of -1 means "center the region in the monitor" — that is
    what the preset rows in the Capture tab send. Any other value is an origin in
    virtual-screen coordinates (a window's bounds); it is clamped so the region
    can never fall outside the monitor.

    Returns ``(region, mode)`` where mode is ``"output"`` for the whole display
    and ``"region"`` for a cropped one.
    """
    mon_left = int(monitor["left"])
    mon_top = int(monitor["top"])
    mon_width = int(monitor["width"])
    mon_height = int(monitor["height"])
    if mon_width <= 0 or mon_height <= 0:
        raise ValueError(f"monitor has invalid size {mon_width}x{mon_height}")

    grab_width = mon_width if int(width) <= 0 else min(int(width), mon_width)
    grab_height = mon_height if int(height) <= 0 else min(int(height), mon_height)
    grab_width = max(1, grab_width)
    grab_height = max(1, grab_height)

    if int(left) < 0 or int(top) < 0:
        x = mon_left + (mon_width - grab_width) // 2
        y = mon_top + (mon_height - grab_height) // 2
        centered = True
    else:
        x = min(max(int(left), mon_left), mon_left + mon_width - grab_width)
        y = min(max(int(top), mon_top), mon_top + mon_height - grab_height)
        centered = False

    if centered and grab_width == mon_width and grab_height == mon_height:
        mode = "output"
    else:
        mode = "region"
    return {"left": x, "top": y, "width": grab_width, "height": grab_height}, mode


class PythonDesktopCapture:
    """``mss``-backed capture worker with the native object's interface."""

    def __init__(
        self,
        sct_factory: Callable[[], Any] | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._sct_factory = sct_factory or self._default_sct_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._sequence = 0
        self._frame: dict[str, Any] | None = None
        self._frames_captured = 0
        self._last_frame_at: float | None = None
        self._frame_times: deque[float] = deque(maxlen=600)
        self._running = False
        self._error = ""
        self._mode = "output"
        self._size = (0, 0)
        self._adapter_name = "Python (mss)"
        self._notice = CAPTURE_NOTICE
        self._requested_fps = 0

    # -- mss plumbing ------------------------------------------------------
    @staticmethod
    def _default_sct_factory() -> Any:
        if _mss is None:
            raise RuntimeError(MISSING_MSS_MESSAGE)
        # mss 10 renamed the entry point to MSS(); older releases only have mss().
        # Both open the platform backend, which raises when no display is reachable.
        constructor = getattr(_mss, "MSS", None) or getattr(_mss, "mss", None)
        if constructor is None:  # pragma: no cover - defensive
            raise RuntimeError(MISSING_MSS_MESSAGE)
        return constructor()

    @staticmethod
    def _open(factory: Callable[[], Any]) -> Any:
        """Create a handle and normalise the ``mss`` import error."""
        try:
            return factory()
        except ImportError as exc:  # a partially installed environment
            raise RuntimeError(MISSING_MSS_MESSAGE) from exc

    def _with_handle(self, factory: Callable[[], Any], work: Callable[[Any], Any]) -> Any:
        handle = self._open(factory)
        try:
            return work(handle)
        finally:
            closer = getattr(handle, "close", None)
            if callable(closer):
                closer()

    # -- displays ----------------------------------------------------------
    def enumerate_outputs(self) -> list[dict[str, Any]]:
        """One entry per physical monitor, shaped like the native enumeration."""
        factory = self._sct_factory

        def collect(sct: Any) -> list[dict[str, Any]]:
            monitors = _monitors(sct)
            outputs: list[dict[str, Any]] = []
            # monitors[0] is the bounding box of every screen; 1.. are physical.
            for index, monitor in enumerate(monitors[1:]):
                outputs.append(
                    {
                        "adapter_index": 0,
                        "output_index": index,
                        "adapter_name": self._adapter_name,
                        "output_name": monitor.get("name") or f"Monitor {index + 1}",
                        "left": int(monitor["left"]),
                        "top": int(monitor["top"]),
                        "width": int(monitor["width"]),
                        "height": int(monitor["height"]),
                        "attached_to_desktop": True,
                    }
                )
            return outputs

        try:
            return list(self._with_handle(factory, collect))
        except RuntimeError as exc:
            # No display to open (RDP, headless session, a driver reset): the Capture
            # tab shows "No display output found" plus this reason instead of failing
            # to build. The window still has to open when a monitor is missing.
            with self._lock:
                if not self._error:
                    self._error = f"Could not open a display: {exc}"
            return []
        except Exception as exc:  # mss raises its own error types
            with self._lock:
                if not self._error:
                    self._error = f"Could not open a display: {exc}"
            return []

    def _monitor_for(self, sct: Any, output_index: int) -> dict[str, Any]:
        monitors = _monitors(sct)
        physical = monitors[1:]
        if not physical:
            raise RuntimeError(
                "no monitor reported by mss; is a display attached and the session interactive?"
            )
        index = int(output_index)
        if not 0 <= index < len(physical):
            index = 0  # a stale selection after a monitor was unplugged
        return physical[index]

    # -- lifecycle ---------------------------------------------------------
    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(
        self,
        adapter_index: int = 0,
        output_index: int = 0,
        left: int = -1,
        top: int = -1,
        width: int = 0,
        height: int = 0,
        target_fps: int = 60,
    ) -> None:
        """Start grabbing. Raises RuntimeError with an install hint when mss is absent.

        ``adapter_index`` is accepted for signature parity and ignored: mss has no
        notion of adapters, every output is reachable from any adapter.
        """
        del adapter_index
        with self._lock:
            if self._running:
                raise RuntimeError("capture is already running")
            if _mss is None and self._sct_factory is None:
                raise RuntimeError(MISSING_MSS_MESSAGE)

        factory = self._sct_factory

        # Resolve the region up front so a bad monitor selection fails here, in the
        # caller's thread, rather than silently inside the worker.
        def resolve(sct: Any) -> tuple[dict[str, int], str]:
            monitor = self._monitor_for(sct, output_index)
            return plan_region(monitor, left, top, width, height)

        region, mode = self._with_handle(factory, resolve)

        fps = int(target_fps) if int(target_fps or 0) > 0 else 60
        fps = max(1, min(fps, MAX_FPS))
        notice = CAPTURE_NOTICE
        if int(target_fps or 0) > MAX_FPS:
            notice = (
                f"Requested {int(target_fps)} FPS; Python capture is capped at {MAX_FPS} FPS. "
                + CAPTURE_NOTICE
            )

        with self._lock:
            self._sequence = 0
            self._frame = None
            self._frames_captured = 0
            self._last_frame_at = None
            self._frame_times.clear()
            self._error = ""
            self._mode = mode
            self._size = (region["width"], region["height"])
            self._notice = notice
            self._requested_fps = int(target_fps or 0)
            self._stop.clear()
            self._running = True

        self._thread = threading.Thread(
            target=self._capture_loop,
            args=(factory, region, 1.0 / fps),
            name="python-capture",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal the worker and join it. Safe to call twice."""
        with self._lock:
            thread = self._thread
            self._running = False
        self._stop.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            self._thread = None

    def close(self) -> None:
        self.stop()

    # -- data --------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            times = [t for t in self._frame_times if now - t <= FPS_WINDOW_S]
            fps = 0.0
            if len(times) >= 2:
                span = max(times[-1] - times[0], 1e-6)
                fps = (len(times) - 1) / span
            age_ms = 0
            if self._last_frame_at is not None:
                age_ms = int(max(0.0, now - self._last_frame_at) * 1000)
            width, height = self._size
            return {
                "running": self._running,
                "capture_fps": float(fps),
                "frames_captured": int(self._frames_captured),
                "acquire_timeouts": 0,
                "access_lost_count": 0,
                "last_frame_age_ms": age_ms,
                "capture_width": int(width),
                "capture_height": int(height),
                "adapter_name": self._adapter_name,
                "last_error": self._error,
                "capture_mode": self._mode,
                "notice": self._notice,
            }

    def latest_sequence(self) -> int:
        with self._lock:
            return int(self._sequence)

    def latest_frame(self) -> dict[str, Any] | None:
        with self._lock:
            frame = self._frame
        if frame is None:
            return None
        return dict(frame)  # the BGRA bytes are immutable, so the dict copy is enough

    # -- worker ------------------------------------------------------------
    def _capture_loop(
        self, factory: Callable[[], Any], region: dict[str, int], interval: float
    ) -> None:
        sct: Any = None
        failures = 0
        exit_error = ""
        try:
            sct = self._open(factory)
            next_deadline = self._clock()
            while not self._stop.is_set():
                try:
                    shot = sct.grab(region)  # type: ignore[attr-defined]
                except Exception as exc:  # transient display/DPMS hiccup
                    failures += 1
                    if failures > MAX_GRAB_FAILURES:
                        raise RuntimeError(f"screen grab failed {failures} times: {exc}") from exc
                    self._stop.wait(0.05)
                    continue
                failures = 0
                data = _shot_bgra(shot)
                width, height = _shot_size(shot)
                now = self._clock()
                with self._lock:
                    self._sequence += 1
                    self._frames_captured += 1
                    self._last_frame_at = now
                    self._frame_times.append(now)
                    self._size = (width, height)
                    self._frame = {
                        "sequence": self._sequence,
                        "timestamp_us": int(now * 1_000_000),
                        "arrival_us": int(now * 1_000_000),
                        "width": int(width),
                        "height": int(height),
                        "stride": int(width * 4),
                        "pointer_visible": False,
                        "pointer_x": 0,
                        "pointer_y": 0,
                        "bgra": data,
                    }
                next_deadline += interval
                remaining = next_deadline - self._clock()
                if remaining > 0:
                    if self._stop.wait(remaining):
                        break
                else:
                    next_deadline = self._clock()  # fell behind: resync, never burst
        except BaseException as exc:  # noqa: BLE001 - reported through stats()
            exit_error = str(exc) or exc.__class__.__name__
        finally:
            if sct is not None:
                closer = getattr(sct, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:  # pragma: no cover - best effort
                        pass
            with self._lock:
                self._running = False
                if exit_error:
                    self._error = exit_error
