"""key_indices: the handles a curve needs, for any length and shape, within a tolerance."""

from __future__ import annotations

import math
import random

import pytest

from fps_coach.curve_sampling import KEY_TOLERANCE, key_indices


def _segment_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _max_error(xs, ys, kept) -> float:
    """Largest distance from any dropped point to the polyline through the kept points."""
    worst = 0.0
    for i in range(len(xs)):
        if i in kept:
            continue
        b = min(k for k in kept if k > i)  # the nearest kept neighbours on each side
        a = max(k for k in kept if k < i)
        worst = max(worst, _segment_distance(xs[i], ys[i], xs[a], ys[a], xs[b], ys[b]))
    return worst


def test_short_inputs_are_returned_whole() -> None:
    assert key_indices([], []) == []
    assert key_indices([0.5], [0.5]) == [0]
    assert key_indices([0.0, 1.0], [0.0, 1.0]) == [0, 1]


def test_straight_line_needs_only_its_ends() -> None:
    xs = [i / 99 for i in range(100)]
    assert key_indices(xs, xs) == [0, 99]


def test_ends_are_always_kept() -> None:
    rng = random.Random(1)
    xs = [i / 49 for i in range(50)]
    ys = [rng.random() for _ in range(50)]
    kept = key_indices(xs, ys)
    assert kept[0] == 0 and kept[-1] == 49


def test_every_dropped_point_is_within_tolerance() -> None:
    rng = random.Random(7)
    for n in (5, 20, 60, 256):
        xs = sorted(rng.random() for _ in range(n))
        ys = [rng.random() for _ in range(n)]
        kept = set(key_indices(xs, ys, KEY_TOLERANCE))
        assert _max_error(xs, ys, kept) <= KEY_TOLERANCE + 1e-12


def test_a_smooth_curve_is_kept_within_its_tolerance() -> None:
    xs = [i / 199 for i in range(200)]
    ys = [0.5 + 0.4 * math.sin(3 * x) for x in xs]
    kept = set(key_indices(xs, ys))
    assert len(kept) < 200
    assert _max_error(xs, ys, kept) <= KEY_TOLERANCE + 1e-12


def test_tighter_tolerance_keeps_at_least_as_many_points() -> None:
    xs = [i / 299 for i in range(300)]
    ys = [0.5 + 0.4 * math.sin(7 * x) * x for x in xs]
    counts = [len(key_indices(xs, ys, tol)) for tol in (0.1, 0.02, 0.005, 0.0005)]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_zero_tolerance_keeps_every_bend() -> None:
    xs = [i / 9 for i in range(10)]
    ys = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
    assert key_indices(xs, ys, 0.0) == list(range(10))


def test_a_vertical_step_keeps_both_sides() -> None:
    xs = [0.0, 0.5, 0.5, 1.0]
    ys = [0.1, 0.1, 0.9, 0.9]
    kept = key_indices(xs, ys)
    assert 1 in kept and 2 in kept


def test_identical_points_collapse_to_the_ends() -> None:
    assert key_indices([0.5] * 40, [0.5] * 40) == [0, 39]


def test_very_long_curves_are_handled() -> None:
    n = 2000
    xs = [i / (n - 1) for i in range(n)]
    ys = [0.5 + 0.3 * math.sin(40 * x) for x in xs]
    kept = key_indices(xs, ys)
    assert 2 < len(kept) < n


def test_mismatched_lengths_are_rejected() -> None:
    with pytest.raises(ValueError):
        key_indices([0.0, 1.0], [0.0])
