"""Visual model simplification retains sampled details with a minimal candidate count."""
from __future__ import annotations

import itertools
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from fps_coach.curve_sampling import KEY_TOLERANCE, _distance_to_segment, balanced_indices


def _max_sample_error(xs, ys, indices):
    worst = 0.0
    for a, b in zip(indices, indices[1:], strict=False):
        for i in range(a + 1, b):
            worst = max(worst, _distance_to_segment(xs[i], ys[i], xs[a], ys[a], xs[b], ys[b]))
    return worst


def _brute_minimum(xs, ys, tolerance):
    for count in range(2, len(xs) + 1):
        for middle in itertools.combinations(range(1, len(xs) - 1), count - 2):
            chosen = (0, *middle, len(xs) - 1)
            if _max_sample_error(xs, ys, chosen) <= tolerance:
                return count
    raise AssertionError("all samples must always be representable")


def test_linear_model_needs_exactly_two_points():
    xs = [i / 128 for i in range(129)]
    assert balanced_indices(xs, xs) == [0, 128]


def test_small_curves_match_independent_exhaustive_minimum():
    rng = random.Random(96)
    for _ in range(35):
        xs = [i / 7 for i in range(8)]
        ys = [rng.random() for _ in xs]
        tolerance = rng.uniform(0.01, 0.3)
        selected = balanced_indices(xs, ys, tolerance)
        assert _max_sample_error(xs, ys, selected) <= tolerance
        assert len(selected) == _brute_minimum(xs, ys, tolerance)


def test_more_curved_models_receive_more_points_without_hardcoded_count():
    xs = [i / 128 for i in range(129)]
    straight = balanced_indices(xs, xs)
    mild = balanced_indices(xs, [x**1.1 for x in xs])
    curved = balanced_indices(xs, [x**1.5 for x in xs])
    assert len(straight) == 2
    assert len(straight) < len(mild) < len(curved) <= 12
    assert _max_sample_error(xs, [x**1.5 for x in xs], curved) <= KEY_TOLERANCE
    # Positions are balanced rather than retaining a nearly overlapping end point.
    assert min(xs[b] - xs[a] for a, b in zip(curved, curved[1:], strict=False)) > 0.04


@pytest.mark.parametrize("gamma,tremor", [(0.72, 0), (0.8, 1), (0.9, 1), (1, 0), (1, 1), (1.5, 1)])
def test_reference_samples_keep_their_details(gamma, tremor):
    xs = sorted({i / 128 for i in range(129)} | {0.8 * (i / 32) + 0.2 for i in range(1, 32)})
    ys = [x**gamma * (1 - 0.12 * tremor * (1 - x)) for x in xs]
    selected = balanced_indices(xs, ys)
    assert selected[0] == 0 and selected[-1] == len(xs) - 1
    assert _max_sample_error(xs, ys, selected) <= KEY_TOLERANCE + 1e-12
    assert 2 <= len(selected) <= 12
    assert all(math.isfinite(xs[i]) and math.isfinite(ys[i]) for i in selected)


def test_invalid_inputs_and_small_curves():
    assert balanced_indices([], []) == []
    assert balanced_indices([0], [1]) == [0]
    assert balanced_indices([0, 1], [0, 1]) == [0, 1]
    with pytest.raises(ValueError):
        balanced_indices([0, 1], [0])
    with pytest.raises(ValueError):
        balanced_indices([0, 1], [0, 1], -1)


def test_native_and_python_simplifiers_match(tmp_path):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ not available")
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "probe.cpp"
    exe = tmp_path / "probe"
    source.write_text(r'''
#include <iostream>
#include "movement_analyzer.cpp"
int main(int, char** argv) {
    const double gamma = std::stod(argv[1]), tremor = std::stod(argv[2]);
    std::vector<double> xs, ys;
    for (int i = 0; i <= 128; ++i) xs.push_back(static_cast<double>(i) / 128.0);
    for (int i = 1; i < 32; ++i) xs.push_back(0.8 * (static_cast<double>(i) / 32.0) + 0.2);
    std::sort(xs.begin(), xs.end());
    xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    for (const auto x : xs) ys.push_back(std::pow(x, gamma) * (1.0 - 0.12 * tremor * (1.0 - x)));
    for (const auto i : fpscoach::balanced_indices(xs, ys)) std::cout << i << ' ';
}
''', encoding="utf-8")
    subprocess.run([compiler, "-std=c++20", "-O2", "-pthread", "-I", str(root / "native/include"),
                    "-I", str(root / "native/src"), str(source), "-o", str(exe)],
                   check=True, capture_output=True, timeout=60)
    xs = sorted({i / 128 for i in range(129)} | {0.8 * (i / 32) + 0.2 for i in range(1, 32)})
    for gamma, tremor in [(0.72, 0), (0.8, 1), (0.9, 1), (1, 0), (1, 1), (1.5, 1)]:
        ys = [x**gamma * (1 - 0.12 * tremor * (1 - x)) for x in xs]
        output = subprocess.check_output([str(exe), str(gamma), str(tremor)], text=True, timeout=10)
        assert list(map(int, output.split())) == balanced_indices(xs, ys)
