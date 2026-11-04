"""Generated point count and full-domain normalized-output error guarantees."""
from __future__ import annotations

import bisect
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from fps_coach.curve_model import (
    CURVE_OUTPUT_ERROR_TOLERANCE,
    _candidate_positions,
    model_output,
    optimize_model_points,
    segment_max_error,
)

SPEEDS = [10.0, 20.0, 30.0, 40.0, 50.0, 100.0]
PARAMETERS = [(0.72, 0.0), (0.72, 1.0), (0.8, 0.0), (0.9, 1.0),
              (0.98, 0.0), (1.0, 0.0), (1.0, 1.0), (1.01, 0.0),
              (1.2, 0.5), (1.5, 0.0), (1.5, 1.0)]


def _sample_max_error(points, gamma, tremor, count=20_000):
    xs = [p[0] for p in points]
    worst = 0.0
    for i in range(count + 1):
        x = i / count
        segment = min(len(points) - 2, bisect.bisect_right(xs, x) - 1)
        a, b = points[segment], points[segment + 1]
        y = a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
        worst = max(worst, abs(model_output(x, gamma, tremor) - y))
    return worst


@pytest.mark.parametrize(("gamma", "tremor"), PARAMETERS)
def test_full_domain_error_and_sensible_endpoints(gamma, tremor):
    points, reported_error = optimize_model_points(gamma, tremor, SPEEDS, 100.0)
    assert points[0] == (0.0, 0.0)
    assert points[-1] == (1.0, 1.0)
    assert len(points) <= 256
    assert all(a[0] < b[0] and a[1] <= b[1] for a, b in zip(points, points[1:], strict=False))
    sampled_error = _sample_max_error(points, gamma, tremor)
    assert sampled_error <= CURVE_OUTPUT_ERROR_TOLERANCE
    assert sampled_error <= reported_error + 1e-12
    assert reported_error <= CURVE_OUTPUT_ERROR_TOLERANCE
    if gamma == 1.0 and tremor == 0.0:
        assert len(points) == 2
    elif gamma in (0.72, 1.5):
        assert len(points) > 2


def test_inflection_curve_checks_both_error_extrema():
    # This case changes convexity inside the domain; checking only its midpoint
    # or the chord endpoints misses an extremum.
    gamma, tremor = 0.9, 1.0
    a, b = 0.01, 1.0
    error = segment_max_error(a, b, gamma, tremor)
    dense = 0.0
    # Independently sample the actual interval rather than extrapolating to zero.
    for i in range(20_001):
        x = a + (b - a) * i / 20_000
        chord = model_output(a, gamma, tremor) + (model_output(b, gamma, tremor)
                  - model_output(a, gamma, tremor)) * (x - a) / (b - a)
        dense = max(dense, abs(model_output(x, gamma, tremor) - chord))
    assert error >= dense - 1e-12
    assert error == pytest.approx(dense, abs=1e-8)


def test_extreme_and_random_model_parameters_remain_within_tolerance():
    rng = random.Random(95)
    for _ in range(40):
        gamma = rng.uniform(0.72, 1.5)
        tremor = rng.uniform(0.0, 1.0)
        points, error = optimize_model_points(gamma, tremor, SPEEDS, 100.0)
        assert error <= CURVE_OUTPUT_ERROR_TOLERANCE
        assert _sample_max_error(points, gamma, tremor, 3_000) <= CURVE_OUTPUT_ERROR_TOLERANCE


def test_count_is_minimal_over_candidate_set_against_independent_lower_bound():
    # Any valid segment must pass these sample checks; the resulting shortest
    # path is a LOWER bound on any fully valid candidate path. Equality with the
    # proven-valid result therefore confirms its minimum candidate-set count.
    gamma, tremor = 1.2, 0.5
    points, _ = optimize_model_points(gamma, tremor, SPEEDS, 100.0)
    xs = _candidate_positions(SPEEDS, 100.0)
    ys = [model_output(x, gamma, tremor) for x in xs]
    distances = [len(xs)] * len(xs)
    distances[0] = 0
    for right in range(1, len(xs)):
        for left in range(right):
            if distances[left] + 1 >= distances[right]:
                continue
            ok = True
            for k in range(1, 17):
                weight = k / 17
                x = xs[left] + (xs[right] - xs[left]) * weight
                if abs(model_output(x, gamma, tremor) - (ys[left] * (1 - weight) + ys[right] * weight)) > 0.001:
                    ok = False
                    break
            if ok:
                distances[right] = distances[left] + 1
    assert distances[-1] == len(points) - 1


def test_empty_model_and_outlier_candidate_safety():
    assert optimize_model_points(1.2, 0.5, [], 0) == ([], 0)
    speeds = [1.0] * 20 + [1e100]
    points, error = optimize_model_points(1.5, 0.0, speeds, 1.0)
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in points)
    assert error <= CURVE_OUTPUT_ERROR_TOLERANCE


def test_native_optimizer_matches_python_for_linear_and_nonlinear_models(tmp_path):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ not available")
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "probe.cpp"
    exe = tmp_path / "probe"
    source.write_text(r'''
#include <iomanip>
#include <iostream>
#include "movement_analyzer.cpp"
int main(int, char** argv) {
    double gamma = std::stod(argv[1]), tremor = std::stod(argv[2]);
    const std::vector<double> speeds{10, 20, 30, 40, 50, 100};
    auto [points, error] = fpscoach::optimize_model_points(gamma, tremor, speeds, 100);
    std::cout << std::setprecision(17) << error << '\n';
    for (const auto& [x, y] : points) std::cout << x << ' ' << y << '\n';
}
''', encoding="utf-8")
    subprocess.run([compiler, "-std=c++20", "-O2", "-pthread", "-I", str(root / "native/include"),
                    "-I", str(root / "native/src"), str(source), "-o", str(exe)],
                   check=True, capture_output=True, timeout=60)
    for gamma, tremor in PARAMETERS:
        lines = subprocess.check_output([str(exe), str(gamma), str(tremor)], text=True, timeout=10).splitlines()
        native_error = float(lines[0])
        native_points = [tuple(map(float, line.split())) for line in lines[1:]]
        points, error = optimize_model_points(gamma, tremor, SPEEDS, 100)
        assert native_error == pytest.approx(error, abs=1e-11)
        assert len(native_points) == len(points)
        for actual, expected in zip(native_points, points, strict=True):
            assert actual == pytest.approx(expected, abs=1e-11)
