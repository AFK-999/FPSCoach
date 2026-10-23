"""Choose the points a curve needs, so the chart shows its shape without a fixed point count.

The chart draws every point of a curve as a line. The handles (the points a user can grab) are
chosen here: the bends that a straight-line curve through them needs to stay within a tolerance
of the full curve. A straight run needs two handles, a wiggly curve needs many, and any length is
handled. Pure standard library, so it is testable without Qt.
"""

from __future__ import annotations

import math

# Largest distance (as a fraction of the 0..1 chart) that a dropped point may be from the curve
# drawn through the kept handles. 0.005 is about 1.4 px on a 274 px plot.
KEY_TOLERANCE = 0.005


def _distance_to_segment(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """Distance from a point to the segment (not the infinite line) through the two end points."""
    dx, dy = x1 - x0, y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def key_indices(xs: list[float], ys: list[float], tolerance: float = KEY_TOLERANCE) -> list[int]:
    """Indices of the points needed to keep a straight-line curve within ``tolerance``.

    Ramer-Douglas-Peucker on the (x, y) points in chart space. The first and last points are always
    kept, and every dropped point lies within ``tolerance`` of the kept polyline (measured to the
    segment, so the guarantee holds at the ends of each segment too). Points must be in
    order of x; equal x values (a vertical step) are allowed.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    n = len(xs)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        x0, y0, x1, y1 = xs[a], ys[a], xs[b], ys[b]
        worst_index, worst_distance = a + 1, -1.0
        for i in range(a + 1, b):
            distance = _distance_to_segment(xs[i], ys[i], x0, y0, x1, y1)
            if distance > worst_distance:
                worst_index, worst_distance = i, distance
        if worst_distance > tolerance:
            keep[worst_index] = True
            stack.append((a, worst_index))
            stack.append((worst_index, b))
    return [i for i, kept in enumerate(keep) if kept]
