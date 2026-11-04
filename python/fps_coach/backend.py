"""Small Python facade over the native performance-critical core.

The fallback analyzer keeps the guide/calibration UI usable on developer
machines. Production Windows builds use ``fpscoach_native`` for analysis and
Desktop Duplication.

Capture has two paths that share one interface: the native Desktop Duplication
extension when it is built, otherwise :class:`fps_coach.python_capture.
PythonDesktopCapture` (``mss`` on a thread), so the Capture tab's live preview
works without a C++ toolchain (rev78).
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Any

try:
    import fpscoach_native as _native
except ImportError:
    _native = None

from . import python_capture
from .curve_sampling import balanced_indices

MAX_ANALYSIS_SAMPLES_PER_TRIAL = 65_536  # preserves a full 1 kHz trial for over a minute
CALIBRATION_WINDOW_TRIALS = 8  # last accepted trials; mirrored in the native analyzer
CURVE_DISTRIBUTION_VERSION = 4
CURVE_SPEED_REFERENCE_QUANTILE = 0.95
CURVE_UNIFORM_ANCHOR_WEIGHT = 0.80

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
        self._recent_trials: deque[tuple[dict[str, float], list[float], int]] = deque(
            maxlen=CALIBRATION_WINDOW_TRIALS
        )
        self._total_trial_count = 0

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
        if (
            timestamp > previous.t
            and math.hypot(x - previous.x, y - previous.y) >= 0.05
            and len(self._trial["samples"]) < MAX_ANALYSIS_SAMPLES_PER_TRIAL
        ):
            self._trial["samples"].append(_Sample(x, y, timestamp))

    def set_target(self, x: float, y: float) -> None:
        """Use the actual target center at click time for moving-target metrics."""
        if self._trial and math.isfinite(x) and math.isfinite(y):
            old = self._trial["target"]
            self._trial["target"] = _Sample(x, y, old.t)

    def end_trial(self, x: float, y: float, timestamp: float) -> bool:
        if not self._trial:
            return False
        samples = self._trial["samples"]
        previous = samples[-1]
        # Always keep the click endpoint without exceeding the memory bound.
        if timestamp > previous.t and math.hypot(x - previous.x, y - previous.y) >= 0.05:
            endpoint = _Sample(x, y, timestamp)
            if len(samples) < MAX_ANALYSIS_SAMPLES_PER_TRIAL:
                samples.append(endpoint)
            else:
                samples[-1] = endpoint
        trial, self._trial = self._trial, None
        samples: list[_Sample] = trial["samples"]
        start: _Sample = trial["start"]
        target: _Sample = trial["target"]
        direct = math.hypot(target.x - start.x, target.y - start.y)
        # A single delivered movement packet is a real observed segment. It
        # can carry a fast/coalesced target movement, but cannot reveal any
        # intermediate corrections; consumers must treat sparse paths as such.
        if len(samples) < 2 or direct < 8 or samples[-1].t <= samples[0].t:
            return False

        ux, uy = (target.x - start.x) / direct, (target.y - start.y) / direct
        path = sum(
            math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(samples, samples[1:], strict=False)
        )
        projections = [((s.x - start.x) * ux + (s.y - start.y) * uy) / direct for s in samples]
        perpendicular = [abs((s.x - start.x) * -uy + (s.y - start.y) * ux) for s in samples]
        backward = sum(b < a - 0.006 for a, b in zip(projections, projections[1:], strict=False))
        duration = samples[-1].t - samples[0].t
        metrics = {
            "path_efficiency": min(1.0, direct / max(path, direct)),
            "overshoot_score": min(1.0, max(0.0, max(projections) - 1.0) * 3.0),
            "underreach_score": min(1.0, max(0.0, 1.0 - projections[-1]) * 2.5),
            "correction_score": min(1.0, backward / 6.0),
            "tremor_score": min(1.0, statistics.fmean(perpendicular) / max(direct * 0.08, 1.0)),
            "mean_endpoint_error_px": math.hypot(samples[-1].x - target.x, samples[-1].y - target.y),
            "mean_duration_ms": duration * 1000.0,
            "mean_speed_px_s": path / duration,
        }
        speed_samples: list[float] = []
        for first, second in zip(samples, samples[1:], strict=False):
            elapsed = second.t - first.t
            if elapsed <= 0.0:
                continue
            speed = math.hypot(second.x - first.x, second.y - first.y) / elapsed
            if math.isfinite(speed) and speed > 0.0:
                speed_samples.append(speed)
        self._recent_trials.append((metrics, speed_samples, len(samples) - 1))
        self._total_trial_count += 1
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
        result: dict[str, float | int] = {
            "trial_count": self._total_trial_count,
            "window_trial_count": len(self._recent_trials),
            "window_capacity": CALIBRATION_WINDOW_TRIALS,
            "curve_distribution_version": CURVE_DISTRIBUTION_VERSION,
        }
        result.update(
            {
                key: statistics.fmean(entry[0][key] for entry in self._recent_trials)
                if self._recent_trials else 0.0
                for key in keys
            }
        )
        return result

    def recommend(self) -> dict[str, Any]:
        summary = self.summary()
        over = float(summary["overshoot_score"])
        under = float(summary["underreach_score"])
        corrections = float(summary["correction_score"])
        tremor = float(summary["tremor_score"])
        gamma = max(0.72, min(1.5, 1 + 0.55 * over + 0.30 * tremor + 0.20 * corrections - 0.35 * under))
        speeds = sorted(speed for _, samples, _ in self._recent_trials for speed in samples)
        points: list[tuple[float, float]] = []
        speed_reference = 0.0
        clipped_speed_count = 0
        if speeds:
            # Sample the continuous heuristic across the domain, then keep the
            # smallest candidate-point set that preserves visible sampled bends.
            # These are model anchors, not the raw cursor samples.
            def quantile(fraction: float) -> float:
                position = fraction * (len(speeds) - 1)
                lower = math.floor(position)
                upper = math.ceil(position)
                weight = position - lower
                return speeds[lower] * (1.0 - weight) + speeds[upper] * weight

            reference_index = math.ceil(CURVE_SPEED_REFERENCE_QUANTILE * len(speeds)) - 1
            speed_reference = speeds[reference_index]
            clipped_speed_count = sum(speed > speed_reference for speed in speeds)
            candidates = {i / 128 for i in range(129)}
            for i in range(1, 32):
                fraction = i / 32
                observed_x = min(1.0, max(0.0, quantile(fraction) / speed_reference))
                candidates.add(CURVE_UNIFORM_ANCHOR_WEIGHT * fraction
                               + (1.0 - CURVE_UNIFORM_ANCHOR_WEIGHT) * observed_x)
            xs = sorted(candidates)
            ys = [(x**gamma) * (1.0 - 0.12 * tremor * (1.0 - x)) for x in xs]
            points = [(xs[index], ys[index]) for index in balanced_indices(xs, ys)]
        sensitivity = max(0.82, min(1.12, 1 - 0.12 * over - 0.05 * corrections + 0.10 * under))
        count = int(summary["window_trial_count"])
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
        captured_count = sum(entry[2] for entry in self._recent_trials)
        density = min(1.0, captured_count / (2 * count)) if count else 0.0
        if count and density < 1.0:
            reasons.append("Sparse movement observations cannot resolve intermediate corrections; repeat with more samples for a clearer estimate.")
        return {
            "points": points,
            "curve_distribution_version": CURVE_DISTRIBUTION_VERSION,
            "captured_sample_count": captured_count,
            "speed_reference_px_s": speed_reference,
            "clipped_speed_count": clipped_speed_count,
            "window_trial_count": count,
            "window_capacity": CALIBRATION_WINDOW_TRIALS,
            "sensitivity_multiplier": sensitivity,
            "confidence": min(1.0, count / CALIBRATION_WINDOW_TRIALS) * 0.75 * density,
            "title": title,
            "reasons": reasons,
        }


class CoreBackend:
    """Owns one capture worker and one calibration analyzer."""

    def __init__(self, capture_factory=None) -> None:
        self.native_available = _native is not None
        native_analyzer = _native.MovementAnalyzer() if _native else None
        # Older installed native extensions may not include the current window and
        # graph-distribution logic.
        # Keep their capture support, but never silently use their stale calibration.
        try:
            native_summary = dict(native_analyzer.summary()) if native_analyzer else {}
        except (AttributeError, TypeError, ValueError, RuntimeError):
            native_summary = {}
        self.uses_python_analyzer = (
            native_summary.get("window_capacity") != CALIBRATION_WINDOW_TRIALS
            or "window_trial_count" not in native_summary
            or native_summary.get("curve_distribution_version") != CURVE_DISTRIBUTION_VERSION
        )
        self.analyzer = _FallbackAnalyzer() if self.uses_python_analyzer else native_analyzer
        if capture_factory is not None:
            # Tests inject a capture double here instead of touching a display.
            self.capture = capture_factory()
        elif _native is not None:
            self.capture = _native.DesktopCapture()
        elif python_capture.available():
            # No native core (FPSCOACH_SKIP_NATIVE installs, developer machines):
            # mss still gives the Capture tab a real-time preview.
            self.capture = python_capture.PythonDesktopCapture()
        else:
            self.capture = None
        self.capture_error = ""

    @property
    def uses_python_capture(self) -> bool:
        """True when live capture runs on mss rather than Desktop Duplication."""
        return isinstance(self.capture, python_capture.PythonDesktopCapture)

    @property
    def core_label(self) -> str:
        if self.native_available:
            return "Native capture, Python analyzer" if self.uses_python_analyzer else "Native C++ core"
        if self.capture is not None:
            return "Python capture (mss)"
        return "Python preview mode"

    def outputs(self) -> list[dict[str, Any]]:
        """Display outputs, or [] with ``capture_error`` set.

        Enumerating displays talks to the OS/Python capture backend, which fails on a
        headless or locked session. Building the Capture page must not depend on that.
        """
        if not self.capture:
            return []
        try:
            found = list(self.capture.enumerate_outputs())
        except Exception as exc:  # noqa: BLE001 - surfaced through capture_error
            self.capture_error = str(exc) or exc.__class__.__name__
            return []
        self.capture_error = ""
        return found

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
                "last_error": (
                    "Live capture is unavailable: the native core is not loaded and "
                    'mss is not installed (pip install -e ".[recorder]").'
                ),
            }
        stats = dict(self.capture.stats())
        if self.capture_error and not stats.get("frames_captured"):
            stats["last_error"] = self.capture_error
        return stats

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
