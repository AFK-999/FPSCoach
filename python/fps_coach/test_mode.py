"""Local, approximate two-profile response simulation; never changes system input.

Profile speeds are normalized to their own maximum and evaluated by linear gain
interpolation. Raw counts/second are mapped to that normalized range using a
user-visible reference; a separate scale converts counts to arena pixels. This is not Custom Curve's proprietary interpolation or
an exact physical DPI/game sensitivity model.
"""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass

from .curve_io import CurvePoint, CurveProfile


def neutral_profile() -> CurveProfile:
    points = [CurvePoint(0.0, 1.0), CurvePoint(100.0, 1.0)]
    return CurveProfile("Neutral 1×", points, deepcopy(points), source_format="simulation")


def _axis_gain(points: list[CurvePoint], fraction: float) -> float:
    if not points:
        return 1.0
    max_speed = max((p.speed for p in points), default=0.0)
    target = max(0.0, min(1.0, fraction)) * max_speed
    if target <= points[0].speed:
        return points[0].multiplier
    for a, b in zip(points, points[1:], strict=False):
        if target <= b.speed:
            if b.speed <= a.speed:
                return b.multiplier
            part = (target - a.speed) / (b.speed - a.speed)
            return a.multiplier + part * (b.multiplier - a.multiplier)
    return points[-1].multiplier


def response(profile: CurveProfile, dx: float, dy: float, dt: float, reference_speed: float) -> tuple[float, float, float, float]:
    """Return count-space displacement and axis gains for one raw movement packet."""
    if not all(math.isfinite(v) for v in (dx, dy, dt, reference_speed)) or dt <= 0 or reference_speed <= 0:
        raise ValueError("Movement timing and reference speed must be positive and finite.")
    speed_fraction = min(1.0, math.hypot(dx, dy) / dt / reference_speed)
    x_gain = _axis_gain(profile.horizontal, speed_fraction)
    y_gain = _axis_gain(profile.vertical, speed_fraction)
    return dx * x_gain, dy * y_gain, x_gain, y_gain


@dataclass(slots=True)
class TestStats:
    attempts: int = 0
    hits: int = 0
    distance_sum: float = 0.0

    def record(self, error: float, radius: float) -> None:
        self.attempts += 1
        self.hits += int(error <= radius)
        self.distance_sum += error

    @property
    def mean_error(self) -> float:
        return self.distance_sum / self.attempts if self.attempts else 0.0


class ComparisonSimulation:
    """Two profile snapshots receive identical deltas and independent scores."""

    def __init__(self, a: CurveProfile, b: CurveProfile, width: float, height: float,
                 reference_speed: float = 1800.0, input_scale: float = 1.0) -> None:
        if not all(math.isfinite(v) and v > 0 for v in (width, height, reference_speed, input_scale)):
            raise ValueError("Arena dimensions and reference speed must be positive and finite.")
        self.profiles = [deepcopy(a), deepcopy(b)]
        self.width, self.height = width, height
        self.reference_speed = reference_speed
        self.input_scale = input_scale
        self.positions = [(width / 2, height / 2), (width / 2, height / 2)]
        self.stats = [TestStats(), TestStats()]
        self.gains = [(1.0, 1.0), (1.0, 1.0)]

    def recenter(self) -> None:
        self.positions = [(self.width / 2, self.height / 2)] * 2

    def move(self, dx: float, dy: float, dt: float) -> None:
        # Compute both responses first, so a rejected input never moves just one cursor.
        results = [response(p, dx, dy, dt, self.reference_speed) for p in self.profiles]
        for i, (out_x, out_y, gx, gy) in enumerate(results):
            x, y = self.positions[i]
            self.positions[i] = (max(0.0, min(self.width, x + out_x * self.input_scale)),
                                 max(0.0, min(self.height, y + out_y * self.input_scale)))
            self.gains[i] = (gx, gy)

    def click(self, x: float, y: float, radius: float) -> tuple[float, float]:
        if not all(math.isfinite(v) for v in (x, y, radius)) or radius < 0:
            raise ValueError("Target must be finite with nonnegative radius.")
        errors = tuple(math.hypot(px - x, py - y) for px, py in self.positions)
        for stats, error in zip(self.stats, errors, strict=True):
            stats.record(error, radius)
        return errors
