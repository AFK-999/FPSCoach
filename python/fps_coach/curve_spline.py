"""B-spline preview helpers for sensitivity graphs.

CC3-style previews use unweighted, clamped uniform splines. The precise
original implementation, corner markers and device settings are not verified,
so the result is an approximation, not the Custom Curve 3 driver output.
"""
from __future__ import annotations

import math


def weighted_spline(points, weights=None, samples_per_span=32):
    """Return finite rational B-spline samples for ordered control points.

    Equal weights produce an unweighted B-spline. This generic helper is not a
    verified implementation of either Custom Curve version's driver.
    """
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2:
        return pts
    n = len(pts)
    if n == 2:
        return pts
    given = list(weights) if weights is not None and len(weights) == n else [0.5] * n
    ws = [max(1e-4, min(1.0, float(w))) if math.isfinite(float(w)) else 0.5 for w in given]
    degree = min(3, n - 1)
    spans = n - degree
    # Open uniform knot vector; repeated end knots make the endpoints exact.
    knots = [0.0] * (degree + 1) + [i / spans for i in range(1, spans)] + [1.0] * (degree + 1)
    homogeneous = [(x * w, y * w, w) for (x, y), w in zip(pts, ws, strict=True)]
    steps = max(1, min(4096, spans * max(2, int(samples_per_span))))
    result = []
    for i in range(steps + 1):
        if i == steps:
            result.append(pts[-1])
            continue
        t = i / steps
        span = min(n - 1, degree + int(t * spans))
        d = [list(homogeneous[span - degree + j]) for j in range(degree + 1)]
        for r in range(1, degree + 1):
            for j in range(degree, r - 1, -1):
                k = span - degree + j
                denominator = knots[k + degree - r + 1] - knots[k]
                alpha = (t - knots[k]) / denominator if denominator else 0.0
                d[j] = [(1 - alpha) * a + alpha * b for a, b in zip(d[j - 1], d[j], strict=True)]
        xx, yy, ww = d[degree]
        result.append((xx / ww, yy / ww))
    return result


def cc3_spline(points, samples_per_span=32):
    """Unweighted CC3-inspired B-spline; stored CC4 weights do not affect it."""
    return weighted_spline(points, samples_per_span=samples_per_span)


def interpolating_segments(points):
    """Shape-preserving cubic segments passing exactly through each input point.

    Each result is either (end,) for a vertical/degenerate straight segment,
    or (first_control, second_control, end) for a cubic Bezier segment. Ordered
    repeated x values are retained as vertical segments: they do not define a
    single-valued sensitivity at that speed. This is a visualization, not the
    Custom Curve driver algorithm.
    """
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2:
        return []
    count = len(pts)
    widths = [pts[i + 1][0] - pts[i][0] for i in range(count - 1)]
    slopes = []
    for i, width in enumerate(widths):
        slope = (pts[i + 1][1] - pts[i][1]) / width if width > 1e-300 else 0.0
        slopes.append(slope if math.isfinite(slope) else 0.0)
    tangents = [0.0] * count
    tangents[0] = slopes[0] if widths[0] > 0 else 0.0
    tangents[-1] = slopes[-1] if widths[-1] > 0 else 0.0
    for i in range(1, count - 1):
        left, right = slopes[i - 1], slopes[i]
        if widths[i - 1] <= 0 or widths[i] <= 0 or left == 0 or right == 0 or (left < 0) != (right < 0):
            continue
        # Weighted harmonic mean, evaluated via normalized weights to avoid
        # overflowing in the sum of two very small/large width values.
        total = widths[i - 1] + widths[i]
        if not math.isfinite(total) or total <= 0:
            continue
        a, b = widths[i - 1] / total, widths[i] / total
        w1, w2 = (2 * b + a) / 3, (b + 2 * a) / 3
        denom = w1 / left + w2 / right
        if math.isfinite(denom) and denom != 0:
            tangent = 1 / denom
            tangents[i] = tangent if math.isfinite(tangent) else 0.0
    segments = []
    for i, width in enumerate(widths):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if width <= 1e-300:
            segments.append(((x1, y1),))
            continue
        segments.append(((x0 + width / 3, y0 + tangents[i] * width / 3),
                         (x1 - width / 3, y1 - tangents[i + 1] * width / 3),
                         (x1, y1)))
    return segments
