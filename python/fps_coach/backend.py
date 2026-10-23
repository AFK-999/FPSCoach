"""Small Python facade over the native performance-critical core.

The fallback analyzer keeps the guide/calibration UI usable on developer
machines. Production Windows builds use ``fpscoach_native`` for analysis and
Desktop Duplication.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Any

try:
    import fpscoach_native as _native
except ImportError:
    _native = None


OLD_NATIVE_WINDOW_NOTE = (
    "This build of the native capture module cannot capture single windows. "
    "Showing the screen area behind the window instead, so windows on top of it will appear."
)


class NativeCoreUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class _Sample:
    x: float
    y: float
    t: float


class _FallbackAnalyzer:
    """Portable reference model; the native model is used in release builds."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._trial: dict[str, Any] | None = None
        self._metrics: list[dict[str, float]] = []

    def begin_trial(
        self, start_x: float, start_y: float, target_x: float, target_y: float, timestamp: float
    ) -> None:
        start = _Sample(start_x, start_y, timestamp)
        self._trial = {
            "start": start,
            "target": _Sample(target_x, target_y, timestamp),
            "samples": [start],
        }

    def add_sample(self, x: float, y: float, timestamp: float) -> None:
        if not self._trial:
            return
        previous = self._trial["samples"][-1]
        if timestamp > previous.t and math.hypot(x - previous.x, y - previous.y) >= 0.05:
            self._trial["samples"].append(_Sample(x, y, timestamp))

    def end_trial(self, x: float, y: float, timestamp: float) -> bool:
        if not self._trial:
            return False
        self.add_sample(x, y, timestamp)
        trial, self._trial = self._trial, None
        samples: list[_Sample] = trial["samples"]
        start: _Sample = trial["start"]
        target: _Sample = trial["target"]
        direct = math.hypot(target.x - start.x, target.y - start.y)
        if len(samples) < 3 or direct < 8 or samples[-1].t <= samples[0].t:
            return False

        ux, uy = (target.x - start.x) / direct, (target.y - start.y) / direct
        path = sum(
            math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(samples, samples[1:])
        )
        projections = [((s.x - start.x) * ux + (s.y - start.y) * uy) / direct for s in samples]
        perpendicular = [abs((s.x - start.x) * -uy + (s.y - start.y) * ux) for s in samples]
        backward = sum(b < a - 0.006 for a, b in zip(projections, projections[1:]))
        duration = samples[-1].t - samples[0].t
        self._metrics.append(
            {
                "path_efficiency": min(1.0, direct / max(path, direct)),
                "overshoot_score": min(1.0, max(0.0, max(projections) - 1.0) * 3.0),
                "underreach_score": min(1.0, max(0.0, 1.0 - projections[-1]) * 2.5),
                "correction_score": min(1.0, backward / 6.0),
                "tremor_score": min(1.0, statistics.fmean(perpendicular) / max(direct * 0.08, 1.0)),
                "mean_endpoint_error_px": math.hypot(samples[-1].x - target.x, samples[-1].y - target.y),
                "mean_duration_ms": duration * 1000.0,
                "mean_speed_px_s": path / duration,
            }
        )
        return True

    def summary(self) -> dict[str, float | int]:
        keys = (
            "path_efficiency",
            "overshoot_score",
            "underreach_score",
            "correction_score",
            "tremor_score",
            "mean_endpoint_error_px",
            "mean_duration_ms",
            "mean_speed_px_s",
        )
        result: dict[str, float | int] = {"trial_count": len(self._metrics)}
        result.update(
            {key: statistics.fmean(item[key] for item in self._metrics) if self._metrics else 0.0 for key in keys}
        )
        return result

    def recommend(self) -> dict[str, Any]:
        summary = self.summary()
        over = float(summary["overshoot_score"])
        under = float(summary["underreach_score"])
        corrections = float(summary["correction_score"])
        tremor = float(summary["tremor_score"])
        gamma = max(0.72, min(1.5, 1 + 0.55 * over + 0.30 * tremor + 0.20 * corrections - 0.35 * under))
        inputs = (0.0, 0.08, 0.2, 0.4, 0.62, 0.82, 1.0)
        points = [(x, (x**gamma) * (1 - 0.12 * tremor * (1 - x))) for x in inputs]
        points[-1] = (1.0, 1.0)
        sensitivity = max(0.82, min(1.12, 1 - 0.12 * over - 0.05 * corrections + 0.10 * under))
        count = int(summary["trial_count"])
        if over > 0.18 or corrections > 0.28:
            title = "Add control through the middle of the curve"
            reasons = ["Reverse corrections suggest reducing mid-speed response."]
        elif under > 0.20:
            title = "Increase low-to-mid speed response slightly"
            reasons = ["The pointer often stopped short of the target."]
        else:
            title = "Keep a near-linear response"
            reasons = ["The paths do not show a strong need for acceleration."]
        if count < 8:
            reasons.append("Complete at least 8-12 trials for a stable recommendation.")
        return {
            "points": points,
            "sensitivity_multiplier": sensitivity,
            "confidence": min(1.0, count / 12.0) * 0.75,
            "title": title,
            "reasons": reasons,
        }


class CoreBackend:
    """Owns one capture worker and one calibration analyzer."""

    def __init__(self) -> None:
        self.native_available = _native is not None
        self.analyzer = _native.MovementAnalyzer() if _native else _FallbackAnalyzer()
        self.capture = _native.DesktopCapture() if _native else None

    @property
    def core_label(self) -> str:
        return "Native C++ core" if self.native_available else "Python preview mode"

    def outputs(self) -> list[dict[str, Any]]:
        if not self.capture:
            return []
        return list(self.capture.enumerate_outputs())

    def start_capture(
        self,
        adapter_index: int,
        output_index: int,
        width: int,
        height: int,
        target_fps: int,
        left: int = -1,
        top: int = -1,
    ) -> None:
        """Start capture. ``left``/``top`` default to -1 (auto-centered region);
        pass a window's screen origin to restrict the capture to that window."""
        if not self.capture:
            raise NativeCoreUnavailable(
                "Desktop Duplication is available only in a Windows build with the native extension."
            )
        self.capture.start(adapter_index, output_index, int(left), int(top), width, height, target_fps)

    def start_window(
        self,
        hwnd: int,
        adapter_index: int,
        output_index: int,
        width: int,
        height: int,
        target_fps: int,
        left: int = -1,
        top: int = -1,
    ) -> str:
        """Capture only the window ``hwnd``, even when other windows cover it.

        Returns "" when window capture is running. Otherwise it returns a note saying why the capture
        fell back to the screen area behind the window, and that fallback can show windows on top of it.
        ``width``/``height``/``left``/``top`` describe that screen area (the window's bounds).
        """
        if not self.capture:
            raise NativeCoreUnavailable(
                "Window capture is available only in a Windows build with the native extension."
            )
        if callable(getattr(self.capture, "start_window", None)):
            self.capture.start_window(
                hwnd=int(hwnd),
                adapter_index=int(adapter_index),
                output_index=int(output_index),
                x=int(left),
                y=int(top),
                width=int(width),
                height=int(height),
                target_fps=int(target_fps),
            )
            return ""
        # Older native build without window capture: show the screen area and say so.
        self.capture.start(adapter_index, output_index, int(left), int(top), width, height, target_fps)
        return OLD_NATIVE_WINDOW_NOTE

    def stop_capture(self) -> None:
        if self.capture:
            self.capture.stop()

    def capture_stats(self) -> dict[str, Any]:
        if not self.capture:
            return {
                "running": False,
                "capture_fps": 0.0,
                "frames_captured": 0,
                "last_error": "Native Windows core is not loaded.",
            }
        return dict(self.capture.stats())

    @property
    def fast_frame_sequence(self) -> bool:
        """True when the native core can report the newest frame number without copying pixels."""
        return bool(self.capture) and callable(getattr(self.capture, "latest_sequence", None))

    def latest_sequence(self) -> int | None:
        """Number of the newest frame (0 before the first). None when the native core cannot say cheaply."""
        if not self.fast_frame_sequence:
            return None
        return int(self.capture.latest_sequence())

    def latest_frame(self) -> dict[str, Any] | None:
        return self.capture.latest_frame() if self.capture else None

    def close(self) -> None:
        self.stop_capture()
