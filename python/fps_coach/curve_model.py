"""Optimize generated model anchors against the continuous calibration model.

This applies only to newly generated recommendations, not imported/user-edited curves.
The minimum is over a documented, finite set of candidate x positions, not over all
possible real-valued positions. The error bound is vertical normalized-output error
for FPS Coach's straight-line chart, not a guarantee about third-party interpolation.
"""
from __future__ import annotations

import math
from collections import deque

CURVE_OUTPUT_ERROR_TOLERANCE = 0.001
_CANDIDATE_GRID_INTERVALS = 256
_EMPIRICAL_CANDIDATE_INTERVALS = 32
_UNIFORM_WEIGHT = 0.8
# Leave room for numerical rounding in root finding and segment comparisons.
_INTERNAL_LIMIT = CURVE_OUTPUT_ERROR_TOLERANCE - 1e-12


def model_output(x: float, gamma: float, tremor: float) -> float:
    return x**gamma * (1.0 - 0.12 * tremor * (1.0 - x))


def _derivative(x: float, gamma: float, damping: float) -> float:
    if x == 0.0:
        if gamma < 1.0:
            return math.inf
        return 1.0 - damping if gamma == 1.0 else 0.0
    return x ** (gamma - 1.0) * (gamma * (1.0 - damping) + (gamma + 1.0) * damping * x)


def segment_max_error(x0: float, x1: float, gamma: float, tremor: float) -> float:
    """Find the largest vertical difference from the model on the whole segment.

    For this model, the second derivative has at most one zero in (0, 1), so
    its first derivative is monotone on at most two intervals. On each interval
    every interior extremum of the secant error is bracketed and bisected.
    """
    y0 = model_output(x0, gamma, tremor)
    y1 = model_output(x1, gamma, tremor)
    slope = (y1 - y0) / (x1 - x0)
    damping = 0.12 * tremor
    boundaries = [x0]
    if damping > 0.0 and gamma < 1.0:
        inflection = (1.0 - gamma) * (1.0 - damping) / ((gamma + 1.0) * damping)
        if x0 < inflection < x1:
            boundaries.append(inflection)
    boundaries.append(x1)
    worst = 0.0
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        dl = _derivative(left, gamma, damping) - slope
        dr = _derivative(right, gamma, damping) - slope
        # A tangent at an interval boundary must also be considered.
        for x in (left, right):
            worst = max(worst, abs(model_output(x, gamma, tremor) - (y0 + slope * (x - x0))))
        if (dl < 0.0 and dr > 0.0) or (dl > 0.0 and dr < 0.0):
            lo, hi = left, right
            for _ in range(52):
                middle = (lo + hi) / 2.0
                dm = _derivative(middle, gamma, damping) - slope
                if (dl < 0.0 and dm < 0.0) or (dl > 0.0 and dm > 0.0):
                    lo = middle
                else:
                    hi = middle
            x = (lo + hi) / 2.0
            worst = max(worst, abs(model_output(x, gamma, tremor) - (y0 + slope * (x - x0))))
    return worst


def _candidate_positions(speeds: list[float], reference: float) -> list[float]:
    # The quadratic grid resolves the high curvature possible near zero.
    candidates = [(i / _CANDIDATE_GRID_INTERVALS) ** 2 for i in range(_CANDIDATE_GRID_INTERVALS + 1)]
    # Add candidates from the measured speed distribution. They can improve the
    # optimum but never replace the full-coverage grid.
    for i in range(1, _EMPIRICAL_CANDIDATE_INTERVALS):
        fraction = i / _EMPIRICAL_CANDIDATE_INTERVALS
        position = fraction * (len(speeds) - 1)
        lower, upper = math.floor(position), math.ceil(position)
        weight = position - lower
        speed = speeds[lower] * (1.0 - weight) + speeds[upper] * weight
        observed_x = min(1.0, max(0.0, speed / reference))
        candidates.append(_UNIFORM_WEIGHT * fraction + (1.0 - _UNIFORM_WEIGHT) * observed_x)
    return sorted(set(candidates))


def optimize_model_points(
    gamma: float, tremor: float, sorted_speeds: list[float], speed_reference: float
) -> tuple[list[tuple[float, float]], float]:
    """Return a minimum-cardinality path through candidate nodes and its maximum error.

    Breadth-first search on a DAG: an edge exists only when the entire model
    segment is within the conservative error limit. Every edge has equal cost,
    so BFS produces the fewest nodes among this finite set of candidates.
    """
    if not sorted_speeds:
        return [], 0.0
    xs = _candidate_positions(sorted_speeds, speed_reference)
    ys = [model_output(x, gamma, tremor) for x in xs]
    last = len(xs) - 1
    previous = [-1] * len(xs)
    previous[0] = 0
    queue = deque([0])
    while queue and previous[last] == -1:
        a = queue.popleft()
        for b in range(last, a, -1):
            if previous[b] != -1:
                continue
            # Cheap safe rejection; acceptance always uses the full extrema test.
            middle = (xs[a] + xs[b]) / 2.0
            midpoint_error = abs(model_output(middle, gamma, tremor) - (ys[a] + ys[b]) / 2.0)
            if midpoint_error > _INTERNAL_LIMIT:
                continue
            if segment_max_error(xs[a], xs[b], gamma, tremor) <= _INTERNAL_LIMIT:
                previous[b] = a
                queue.append(b)
                if b == last:
                    break
    if previous[last] == -1:
        raise RuntimeError("Calibration model could not meet its curve approximation tolerance")
    indices = [last]
    while indices[-1] != 0:
        indices.append(previous[indices[-1]])
    indices.reverse()
    if len(indices) > 256:
        raise RuntimeError("Calibration model needs more points than the export format supports")
    # The shortest path may leave an unnecessarily short final segment. Keep
    # the same minimum count but rebalance neighboring segments to reduce the
    # larger of their errors, making the handles more useful and evenly placed.
    for _ in range(2):
        for position in range(1, len(indices) - 1):
            left, right = indices[position - 1], indices[position + 1]
            current = indices[position]
            best_error = max(segment_max_error(xs[left], xs[current], gamma, tremor),
                             segment_max_error(xs[current], xs[right], gamma, tremor))
            best = current
            for candidate in range(left + 1, right):
                if candidate == current:
                    continue
                first_error = segment_max_error(xs[left], xs[candidate], gamma, tremor)
                if first_error >= best_error:
                    continue
                second_error = segment_max_error(xs[candidate], xs[right], gamma, tremor)
                candidate_error = max(first_error, second_error)
                if candidate_error < best_error:
                    best, best_error = candidate, candidate_error
            indices[position] = best
    points = [(xs[index], ys[index]) for index in indices]
    error = max(segment_max_error(a[0], b[0], gamma, tremor)
                for a, b in zip(points, points[1:], strict=False))
    return points, error
