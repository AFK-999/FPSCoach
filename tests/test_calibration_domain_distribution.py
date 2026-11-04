"""Model anchors cover the domain without pretending they are raw cursor samples."""
from __future__ import annotations

import pytest
from fps_coach.backend import _FallbackAnalyzer
from fps_coach.curve_io import chart_points, recommendation_profile


def _straight_trial(*, outlier=False):
    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0.0, 0.0, 100.0, 0.0, 0.0)
    elapsed = 0.0
    for i in range(1, 50):
        elapsed += 0.000001 if outlier and i == 25 else 0.01
        analyzer.add_sample(float(i * 2), 0.0, elapsed)
    assert analyzer.end_trial(100.0, 0.0, elapsed + 0.01)
    return analyzer


def test_outlier_does_not_squeeze_model_anchors_against_zero():
    analyzer = _straight_trial(outlier=True)
    rec = analyzer.recommend()
    xs = [x for x, _ in rec["points"]]
    assert rec["speed_reference_px_s"] == pytest.approx(200.0)
    assert rec["clipped_speed_count"] >= 1
    # The speed outlier does not create extra dots or artificial bends.
    assert xs == [0.0, 1.0]


def test_even_speed_straight_samples_use_only_two_endpoints():
    points = _straight_trial().recommend()["points"]
    assert points == [(0.0, 0.0), (1.0, 1.0)]


def test_nonlinear_model_is_balanced_and_covers_the_domain():
    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0, 0, 100, 0, 0)
    for i, x in enumerate((20, 65, 130, 110), 1):
        analyzer.add_sample(x, 0, i * .01)
    assert analyzer.end_trial(100, 0, .05)
    points = analyzer.recommend()["points"]
    assert 2 < len(points) <= 12
    assert points[0] == (0.0, 0.0) and points[-1] == (1.0, 1.0)
    assert any(.3 < x < .7 for x, _ in points)
    assert all(b[0] > a[0] for a, b in zip(points, points[1:], strict=False))



def test_generated_profile_and_coach_chart_preserve_every_anchor():
    points = _straight_trial(outlier=True).recommend()["points"]
    recommendation = _straight_trial(outlier=True).recommend()
    profile = recommendation_profile(recommendation)
    assert [p.speed / 100 for p in profile.horizontal] == pytest.approx([x for x, _ in points])
    assert [x for x, _ in chart_points(profile)] == pytest.approx([x for x, _ in points])
    assert len(profile.horizontal) == len(points)
    assert len(profile.vertical) == len(points)


def test_stale_native_with_old_curve_policy_uses_the_new_python_analyzer(monkeypatch):
    from fps_coach.backend import CoreBackend

    class OldAnalyzer:
        def summary(self):
            return {"trial_count": 0, "window_trial_count": 0, "window_capacity": 8}

    class Native:
        MovementAnalyzer = OldAnalyzer
        DesktopCapture = object

    monkeypatch.setattr("fps_coach.backend._native", Native)
    core = CoreBackend()
    assert core.native_available and core.uses_python_analyzer
    assert isinstance(core.analyzer, _FallbackAnalyzer)


def test_non_linear_measurements_still_produce_a_non_linear_curve():
    analyzer = _FallbackAnalyzer()
    for trial in range(8):
        t = float(trial)
        analyzer.begin_trial(0, 0, 100, 0, t)
        for i, (x, y) in enumerate([(20, 5), (65, -8), (130, 7), (110, -5)], 1):
            analyzer.add_sample(x, y, t + i * 0.01)
        assert analyzer.end_trial(100, 0, t + 0.05)
    points = analyzer.recommend()["points"]
    assert any(abs(y - x) > 0.03 for x, y in points[1:-1])
    assert all(b[1] >= a[1] for a, b in zip(points, points[1:], strict=False))


def test_empty_data_produces_no_curve_or_speed_reference():
    rec = _FallbackAnalyzer().recommend()
    assert rec["points"] == []
    assert rec["speed_reference_px_s"] == 0
    assert rec["clipped_speed_count"] == 0


def test_live_calibration_draws_and_exports_the_same_model_anchors():
    from types import SimpleNamespace

    from fps_coach.ui.pages_setup import CalibrationPage
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    analyzer = _straight_trial(outlier=True)
    rec = analyzer.recommend()
    page = CalibrationPage(SimpleNamespace(analyzer=analyzer))
    page._on_session_started()
    page._on_live_recommendation(analyzer.summary(), rec)
    assert page.chart.x_positions() == pytest.approx([x for x, _ in rec["points"]])
    from fps_coach.curve_io import recommendation_profile
    expected = recommendation_profile(rec)
    assert [v * page.chart.axis_y_max for v in page.chart.points] == pytest.approx(
        [p.multiplier for p in expected.horizontal])
    assert len(page.chart.dot_indices()) == len(rec["points"])
    assert "not raw samples" in page.chart.toolTip()
    assert "95th-percentile desktop speed" in page.chart.toolTip()
    assert "model control points" in page.curve_status.text()
    page._on_complete(analyzer.summary(), rec)
    assert [p.speed / 100 for p in page._profile.horizontal] == pytest.approx([x for x, _ in rec["points"]])
    page.close()
    app.processEvents()


def test_native_and_python_distribution_match_for_same_sampled_path(tmp_path):
    """Compile the real native analyzer when a C++ compiler is available."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ not available")
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "probe.cpp"
    exe = tmp_path / "probe"
    source.write_text(r'''
#include "fpscoach/movement_analyzer.hpp"
#include <iomanip>
#include <iostream>
int main() {
    fpscoach::MovementAnalyzer a;
    a.begin_trial(0, 0, 100, 0, 0);
    double t = 0;
    for (int i = 1; i < 50; ++i) {
        t += i == 25 ? 0.000001 : 0.01;
        a.add_sample(i * 2.0, 0, t);
    }
    if (!a.end_trial(100, 0, t + 0.01)) return 1;
    auto r = a.recommend();
    std::cout << std::setprecision(17) << "[" << r.speed_reference_px_s
              << "," << r.clipped_speed_count << ",[";
    for (std::size_t i = 0; i < r.points.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << '[' << r.points[i].first << ',' << r.points[i].second << ']';
    }
    std::cout << "]]";
}
''', encoding="utf-8")
    subprocess.run([compiler, "-std=c++20", "-O2", "-pthread", "-I", str(root / "native/include"),
                    str(root / "native/src/movement_analyzer.cpp"), str(source), "-o", str(exe)],
                   check=True, capture_output=True, timeout=60)
    reference, clipped, native_points = json.loads(subprocess.check_output([str(exe)], text=True, timeout=10))
    fallback = _straight_trial(outlier=True).recommend()
    assert reference == pytest.approx(fallback["speed_reference_px_s"], rel=1e-10)
    assert clipped == fallback["clipped_speed_count"]
    assert len(native_points) == len(fallback["points"])
    for actual, expected in zip(native_points, fallback["points"], strict=True):
        assert actual == pytest.approx(expected, abs=1e-10)


def test_nearest_rank_reference_avoids_interpolating_towards_outlier_at_twenty_samples():
    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0, 0, 100, 0, 0)
    elapsed = 0.0
    for i in range(1, 20):
        elapsed += 0.000001 if i == 10 else 0.01
        analyzer.add_sample(i * 5, 0, elapsed)
    assert analyzer.end_trial(100, 0, elapsed + 0.01)
    rec = analyzer.recommend()
    assert rec["captured_sample_count"] == 20
    assert rec["speed_reference_px_s"] == pytest.approx(500.0)
    assert rec["clipped_speed_count"] >= 1


@pytest.mark.parametrize("version", [2, 3])
def test_old_native_point_policy_uses_python_analyzer_but_keeps_native_capture(monkeypatch, version):
    from fps_coach.backend import CoreBackend

    class OldAnalyzer:
        def summary(self):
            return {"trial_count": 0, "window_trial_count": 0, "window_capacity": 8,
                    "curve_distribution_version": version}

    class Native:
        MovementAnalyzer = OldAnalyzer
        DesktopCapture = object

    monkeypatch.setattr("fps_coach.backend._native", Native)
    core = CoreBackend()
    assert core.native_available and core.uses_python_analyzer
    assert isinstance(core.analyzer, _FallbackAnalyzer)


def test_nonlinear_recommendation_chart_and_exports_keep_selected_points(tmp_path):
    from types import SimpleNamespace

    from fps_coach.curve_io import load_curve, save_curve
    from fps_coach.ui.pages_setup import CalibrationPage
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    analyzer = _FallbackAnalyzer()
    for trial in range(8):
        t = float(trial)
        analyzer.begin_trial(0, 0, 100, 0, t)
        for i, x in enumerate((20, 65, 130, 110), 1):
            analyzer.add_sample(x, 0, t + i * 0.01)
        assert analyzer.end_trial(100, 0, t + 0.05)
    rec = analyzer.recommend()
    assert 2 < len(rec["points"]) <= 12
    page = CalibrationPage(SimpleNamespace(analyzer=analyzer))
    page._on_session_started()
    page._on_live_recommendation(analyzer.summary(), rec)
    assert page.chart.x_positions() == pytest.approx([x for x, _ in rec["points"]])
    from fps_coach.curve_io import recommendation_profile
    expected = recommendation_profile(rec)
    assert [v * page.chart.axis_y_max for v in page.chart.points] == pytest.approx(
        [p.multiplier for p in expected.horizontal])
    assert "visual approximation" in page.chart.toolTip()
    page._on_complete(analyzer.summary(), rec)
    for suffix in ("cc4", "ccurve"):
        saved = save_curve(page._profile, tmp_path / f"model.{suffix}")
        loaded = load_curve(saved)
        assert len(loaded.horizontal) == len(rec["points"])
        assert len(loaded.vertical) == len(rec["points"])
        assert [p.speed / 100 for p in loaded.horizontal] == pytest.approx([x for x, _ in rec["points"]])
    page.close()
    app.processEvents()


def test_tooltip_correctly_tracks_manual_edit_and_undo():
    from types import SimpleNamespace

    from fps_coach.ui.pages_setup import CalibrationPage
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    analyzer = _straight_trial()
    page = CalibrationPage(SimpleNamespace(analyzer=analyzer))
    page._on_complete(analyzer.summary(), analyzer.recommend())
    assert "visual approximation" in page.chart.toolTip()
    xs = page.chart.x_positions()
    edited = list(page.chart.points)
    edited[-1] = 0.9
    page.chart.points = edited
    page._on_curve_adjusted(edited, xs)
    assert "no longer applies" in page.chart.toolTip()
    page.undo()
    assert "visual approximation" in page.chart.toolTip()
    page.close()
    app.processEvents()


def test_outlier_does_not_compress_a_nonlinear_model():
    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0, 0, 100, 0, 0)
    t = 0.0
    for i in range(1, 50):
        t += 0.000001 if i == 25 else 0.01
        analyzer.add_sample(i * 2, 0, t)
    t += .1
    analyzer.add_sample(125, 0, t)
    assert analyzer.end_trial(100, 0, t + .1)
    rec = analyzer.recommend()
    points = rec["points"]
    assert rec["speed_reference_px_s"] == pytest.approx(250)
    assert rec["clipped_speed_count"] >= 1
    assert 2 < len(points) <= 12
    assert any(.3 < x < .7 for x, _ in points)
    assert points[0] == (0.0, 0.0) and points[-1] == (1.0, 1.0)


def test_native_and_fallback_nonlinear_captured_path_match(tmp_path):
    import json
    import shutil
    import subprocess
    from pathlib import Path

    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ not available")
    root = Path(__file__).resolve().parents[1]
    source, exe = tmp_path / "probe.cpp", tmp_path / "probe"
    source.write_text(r'''
#include "fpscoach/movement_analyzer.hpp"
#include <iomanip>
#include <iostream>
int main() {
    fpscoach::MovementAnalyzer a;
    a.begin_trial(0, 0, 100, 0, 0);
    a.add_sample(20, 0, .01); a.add_sample(65, 0, .02);
    a.add_sample(130, 0, .03); a.add_sample(110, 0, .04);
    if (!a.end_trial(100, 0, .05)) return 1;
    const auto r = a.recommend();
    std::cout << std::setprecision(17) << '[';
    for (std::size_t i = 0; i < r.points.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << '[' << r.points[i].first << ',' << r.points[i].second << ']';
    }
    std::cout << ']';
}
''', encoding="utf-8")
    subprocess.run([compiler, "-std=c++20", "-O2", "-pthread", "-I", str(root / "native/include"),
                    str(root / "native/src/movement_analyzer.cpp"), str(source), "-o", str(exe)],
                   check=True, capture_output=True, timeout=60)
    native = json.loads(subprocess.check_output([str(exe)], text=True, timeout=10))
    analyzer = _FallbackAnalyzer()
    analyzer.begin_trial(0, 0, 100, 0, 0)
    for i, x in enumerate((20, 65, 130, 110), 1):
        analyzer.add_sample(x, 0, i * .01)
    assert analyzer.end_trial(100, 0, .05)
    fallback = analyzer.recommend()["points"]
    assert len(native) == len(fallback) > 2
    for actual, expected in zip(native, fallback, strict=True):
        assert actual == pytest.approx(expected, abs=1e-10)
