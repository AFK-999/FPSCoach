"""Qt glue for :mod:`fps_coach.screen_recorder`.

The engine is plain ``threading`` code; this module owns one
:class:`~fps_coach.screen_recorder.ScreenRecorder` and republishes its state as
Qt signals so the Capture tab never touches worker threads directly.

Threading contract:

* ``start``/``stop``/``shutdown``/``poll`` are called from the GUI thread.
* The engine's analysis callback runs on the ``screen-analysis`` thread.  It
  emits :attr:`RecordingService.analysis`, which Qt delivers to GUI-thread
  receivers through the normal queued connection.  The callback is guarded by a
  lock and a closed flag, and it samples the frame (every 8th pixel) so a tick
  costs a fraction of a millisecond.
* :meth:`RecordingService.shutdown` stops and joins the engine's threads before
  the owning widget is destroyed, so no signal can be emitted into a deleted
  QObject.

Retention: unless ``keep`` was requested the scratch file is deleted when the
recording stops (or when the app closes).  With ``keep=True`` the file is moved
to a timestamped ``fpscoach-recording-*.mp4`` name in ``save_dir`` and survives
both stop and shutdown.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from fps_coach import screen_recorder
from fps_coach.screen_recorder import LOGGER

__all__ = ["AnalysisTick", "RecordingService", "RecordingStatus", "RecordingSummary"]


@dataclass(frozen=True, slots=True)
class AnalysisTick:
    """One analysis callback result, delivered on the GUI thread."""

    count: int
    sequence: int
    width: int
    height: int
    mean: float
    at: float


@dataclass(frozen=True, slots=True)
class RecordingStatus:
    """A cheap snapshot of the recorder, polled by the page's timer."""

    running: bool
    frames: int
    seconds: float
    analyses: int
    path: str
    error: str = ""


@dataclass(frozen=True, slots=True)
class RecordingSummary:
    """What a finished recording was, reported once the worker threads are done.

    ``path`` is the saved file when ``kept`` is true, otherwise the deleted
    scratch path (useful for logs).
    """

    frames: int
    seconds: float
    analyses: int
    path: str
    kept: bool = False


class RecordingService(QObject):
    """Owns at most one ``ScreenRecorder`` and mirrors it as Qt signals."""

    started = Signal(str)  # scratch file path
    stopped = Signal(object)  # RecordingSummary
    analysis = Signal(object)  # AnalysisTick
    failed = Signal(str)

    def __init__(self, parent=None, recorder_factory: Callable[..., object] | None = None) -> None:
        super().__init__(parent)
        self._recorder_factory = recorder_factory or screen_recorder.ScreenRecorder
        self._recorder = None
        self._lock = threading.Lock()
        self._ticks = 0
        self._closed = False
        self._last_error = ""
        self._keep = False

    # -- capability --------------------------------------------------------
    @staticmethod
    def availability() -> tuple[bool, str]:
        """Return ``(usable, message)``; ``message`` explains what to install."""
        missing = screen_recorder.missing_dependencies()
        if not missing:
            return True, ""
        return False, (
            "Screen recording needs these optional packages: "
            + ", ".join(missing)
            + '. Install them with: pip install -e ".[recorder]"'
        )

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None

    @property
    def analysis_count(self) -> int:
        with self._lock:
            return self._ticks

    # -- lifecycle ---------------------------------------------------------
    def start(
        self,
        *,
        fps: float = 30.0,
        analysis_interval: float = 2.0,
        monitor_index: int = 1,
        keep: bool = False,
        save_dir: str | None = None,
    ) -> bool:
        """Start recording.  Returns ``False`` (and emits ``failed``) on error.

        ``keep`` keeps the finished video instead of deleting it, and
        ``save_dir`` chooses where it is written (created when missing).
        """
        if self._closed:
            return False
        if self._recorder is not None:
            return True
        usable, message = self.availability()
        if not usable:
            self.failed.emit(message)
            return False
        recorder = None
        target = Path(save_dir).expanduser() if save_dir else None
        try:
            kwargs: dict = {}
            if keep:
                kwargs["keep_temp_file"] = True
            if target is not None:
                kwargs["output_dir"] = target
            recorder = self._recorder_factory(
                fps=float(fps),
                analysis_interval=float(analysis_interval),
                monitor_index=int(monitor_index),
                analysis_callback=self._on_frame,
                join_timeout=3.0,
                **kwargs,
            )
            recorder.start()
        except Exception as exc:  # noqa: BLE001 - reported to the UI, never raised
            self._last_error = str(exc)
            self._safe_cleanup(recorder)
            self.failed.emit(str(exc))
            return False
        with self._lock:
            self._ticks = 0
        self._last_error = ""
        self._keep = bool(keep)
        self._recorder = recorder
        self.started.emit(str(recorder.video_path))
        return True

    def stop(self) -> RecordingSummary | None:
        """Stop, join the workers, and keep or delete the file as requested."""
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return None
        raw = recorder.video_path
        summary = RecordingSummary(
            frames=recorder.frames_written,
            seconds=round(float(recorder.recorded_seconds), 1),
            analyses=self.analysis_count,
            path=str(raw or ""),
        )
        self._safe_cleanup(recorder)
        if self._keep:
            summary = self._with_saved_path(summary, raw)
        self.stopped.emit(summary)
        return summary

    def shutdown(self) -> None:
        """Stop everything without emitting further signals.  Idempotent.

        A recording made with ``keep=True`` still survives a shutdown: the file
        is renamed and left in ``save_dir``.
        """
        self._closed = True
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        raw = recorder.video_path
        self._safe_cleanup(recorder)
        if self._keep:
            self._save_file(raw)

    def poll(self) -> RecordingStatus:
        """Return the current counters, and stop cleanly if capture failed."""
        recorder = self._recorder
        if recorder is None:
            return RecordingStatus(False, 0, 0.0, self.analysis_count, "", self._last_error)
        error = recorder.capture_error
        path = str(recorder.video_path or "")
        if error is not None:
            message = f"Recording stopped: {error}"
            self._last_error = message
            self.stop()
            self.failed.emit(message)
            return RecordingStatus(False, 0, 0.0, self.analysis_count, path, message)
        return RecordingStatus(
            running=recorder.is_running,
            frames=recorder.frames_written,
            seconds=round(float(recorder.recorded_seconds), 1),
            analyses=self.analysis_count,
            path=path,
        )

    # -- internals ---------------------------------------------------------
    def _on_frame(self, frame, sequence: int) -> None:
        """Engine analysis callback; runs on the ``screen-analysis`` thread."""
        with self._lock:
            if self._closed:
                return
            self._ticks += 1
            count = self._ticks
        height, width = frame.shape[:2]
        try:
            mean = float(frame[::8, ::8].mean())
        except Exception:  # noqa: BLE001 - statistics must never kill the timer
            mean = 0.0
        tick = AnalysisTick(
            count=count,
            sequence=int(sequence),
            width=int(width),
            height=int(height),
            mean=mean,
            at=time.monotonic(),
        )
        try:
            self.analysis.emit(tick)  # queued to the GUI thread by Qt
        except RuntimeError:  # pragma: no cover - receiver already gone
            pass

    def _with_saved_path(self, summary: RecordingSummary, raw) -> RecordingSummary:
        saved = self._save_file(raw)
        if saved is None:
            return summary
        return RecordingSummary(
            frames=summary.frames,
            seconds=summary.seconds,
            analyses=summary.analyses,
            path=str(saved),
            kept=True,
        )

    @staticmethod
    def _save_file(raw) -> Path | None:
        """Rename a kept scratch file to ``fpscoach-recording-<stamp>.mp4``."""
        if raw is None:
            return None
        path = Path(raw)
        if not path.exists():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = path.with_name(f"fpscoach-recording-{stamp}{path.suffix}")
        counter = 2
        while target.exists():
            target = path.with_name(f"fpscoach-recording-{stamp}-{counter}{path.suffix}")
            counter += 1
        try:
            path.replace(target)
        except OSError:
            LOGGER.exception("could not rename the kept recording %s", path)
            return path
        LOGGER.info("recording saved to %s", target)
        return target

    @staticmethod
    def _safe_cleanup(recorder) -> None:
        if recorder is None:
            return
        try:
            recorder.cleanup()
        except Exception:  # noqa: BLE001 - never raise during teardown
            LOGGER.exception("recorder cleanup failed")
