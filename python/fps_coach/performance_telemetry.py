"""Ephemeral in-app click and pointer telemetry for the optional curve preview.

Times are monotonic software-event timestamps, not hardware latency or reaction
neurology. Accuracy is visible-target hits divided by all recorded attempts.
"""
from __future__ import annotations

import math
import statistics
from collections import deque


class PerformanceTelemetry:
    """Collect a bounded recent window; no global input, screen capture or persistence."""

    def __init__(self, source: str, capacity: int = 32):
        if source not in ("calibration", "training"):
            raise ValueError("Unknown telemetry source")
        self.source = source
        self.capacity = capacity
        self.reset()

    def reset(self) -> None:
        self._attempts = deque(maxlen=self.capacity)
        self._target_started = None
        self._previous = None
        self._path = 0.0
        self._motion_start = None
        self._motion_end = None
        self._origin = None
        self._sample_count = 0
        self.total_attempts = 0
        self.total_hits = 0
        self._error_sum = self._radius_error_sum = 0.0
        self._radius_error_count = 0
        self._hit_click_ms_sum = 0.0
        self._tracking_count = self._tracking_on_target = 0
        self._tracking_error_sum = self._tracking_ratio_sum = 0.0

    def begin_target(self, timestamp: float, x: float | None = None, y: float | None = None) -> None:
        self._target_started = timestamp
        self._previous = None
        self._path = 0.0
        self._motion_start = self._motion_end = None
        self._origin = None
        self._sample_count = 0
        if x is not None and y is not None:
            self.move(x, y, timestamp)

    def end_target(self) -> None:
        """Discard temporary coordinates without discarding the bounded attempt summaries."""
        self._target_started = None
        self._previous = self._origin = None
        self._path = 0.0
        self._motion_start = self._motion_end = None
        self._sample_count = 0

    def break_path(self) -> None:
        """Drop a discontinuous path (e.g. pointer left the task), but keep target timing."""
        self._previous = self._origin = None
        self._path = 0.0
        self._motion_start = self._motion_end = None
        self._sample_count = 0

    def move(self, x: float, y: float, timestamp: float) -> None:
        if (self._target_started is None or not all(math.isfinite(v) for v in (x, y, timestamp))
                or timestamp < self._target_started):
            return
        if self._previous is not None:
            px, py, pt = self._previous
            if timestamp <= pt:
                return
            distance = math.hypot(x - px, y - py)
            if distance > 0.0:
                self._path += distance
                self._motion_start = pt if self._motion_start is None else self._motion_start
                self._motion_end = timestamp
                self._sample_count += 1
        else:
            self._origin = (x, y)
        self._previous = (x, y, timestamp)

    def click(self, x: float, y: float, target_x: float, target_y: float,
              radius: float, timestamp: float, *, target_speed_px_s: float | None = None) -> dict | None:
        if (self._target_started is None or not all(math.isfinite(v) for v in
                (x, y, target_x, target_y, radius, timestamp)) or timestamp < self._target_started or radius < 0
                or (self._previous is not None and timestamp < self._previous[2])):
            return None
        self.move(x, y, timestamp)
        error = math.hypot(x - target_x, y - target_y)
        hit = error <= radius
        self.total_attempts += 1
        self.total_hits += int(hit)
        duration_ms = (timestamp - self._target_started) * 1000
        self._error_sum += error
        if radius > 0:
            self._radius_error_sum += error / radius
            self._radius_error_count += 1
        if hit:
            self._hit_click_ms_sum += duration_ms
        elapsed = (self._motion_end - self._motion_start
                   if self._motion_start is not None and self._motion_end is not None else 0)
        speed = self._path / elapsed if elapsed > 0 and self._sample_count > 0 else None
        direct = math.hypot(x - self._origin[0], y - self._origin[1]) if self._origin else 0
        efficiency = min(1.0, direct / self._path) if self._path > 0 else None
        self._attempts.append({"hit": hit, "click_ms": duration_ms, "error_px": error,
                               "speed_px_s": speed, "path_efficiency": efficiency,
                               "target_radius_px": radius,
                               "target_speed_px_s": (target_speed_px_s if target_speed_px_s is not None
                                                     and math.isfinite(target_speed_px_s) and target_speed_px_s >= 0 else None),
                               "observed_path_px": self._path, "movement_samples": self._sample_count})
        return self.snapshot()

    def observe_tracking(self, x: float, y: float, target_x: float, target_y: float,
                         radius: float, timestamp: float) -> None:
        """Summarize delivered in-app pointer events against a moving target.

        Event-weighted, not time-weighted: stationary periods are not sampled.
        No coordinates or individual tracking samples are retained.
        """
        if (self._target_started is None or not all(math.isfinite(v) for v in
                (x, y, target_x, target_y, radius, timestamp)) or radius <= 0
                or timestamp < self._target_started):
            return
        error = math.hypot(x - target_x, y - target_y)
        self._tracking_count += 1
        self._tracking_on_target += int(error <= radius)
        self._tracking_error_sum += error
        self._tracking_ratio_sum += error / radius

    def session_summary(self) -> dict:
        """Full-session aggregates only; never includes a pointer path or attempts."""
        n, hits, track = self.total_attempts, self.total_hits, self._tracking_count
        return {
            "attempts": n, "hits": hits,
            "accuracy_pct": 100 * hits / n if n else None,
            "mean_hit_click_ms": self._hit_click_ms_sum / hits if hits else None,
            "mean_endpoint_error_px": self._error_sum / n if n else None,
            "mean_endpoint_error_radii": (self._radius_error_sum / self._radius_error_count
                                          if self._radius_error_count else None),
            "tracking_event_count": track,
            "tracking_on_target_pct": 100 * self._tracking_on_target / track if track else None,
            "tracking_mean_error_px": self._tracking_error_sum / track if track else None,
            "tracking_mean_error_radii": self._tracking_ratio_sum / track if track else None,
        }

    def snapshot(self) -> dict:
        attempts = list(self._attempts)
        hits = [a for a in attempts if a["hit"]]
        def avg(key, items):
            values = [a[key] for a in items if a[key] is not None]
            return statistics.fmean(values) if values else None
        return {
            "source": self.source,
            "window_attempts": len(attempts),
            "window_hits": len(hits),
            "window_accuracy_pct": 100 * len(hits) / len(attempts) if attempts else None,
            "total_attempts": self.total_attempts,
            "total_hits": self.total_hits,
            "mean_hit_click_ms": avg("click_ms", hits),
            "mean_endpoint_error_px": avg("error_px", attempts),
            "mean_hit_speed_px_s": avg("speed_px_s", hits),
            "mean_hit_path_efficiency": avg("path_efficiency", hits),
            "window_capacity": self.capacity,
            "recent_attempts": [dict(a) for a in attempts],
            "timing_definition": "target visible, trial restarted or collection enabled mid-target to click; monotonic app event time",
            "accuracy_definition": "clicks inside the visible target / all recorded clicks",
        }
