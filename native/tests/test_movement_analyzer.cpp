#include "fpscoach/movement_analyzer.hpp"

#include <cassert>
#include <cmath>
#include <iostream>

using fpscoach::MovementAnalyzer;

namespace {

void add_straight_trial(MovementAnalyzer& analyzer, const double time_offset) {
    analyzer.begin_trial(0.0, 0.0, 100.0, 0.0, time_offset);
    for (int i = 1; i <= 10; ++i) {
        analyzer.add_sample(i * 10.0, 0.0, time_offset + i * 0.01);
    }
    assert(analyzer.end_trial(100.0, 0.0, time_offset + 0.11));
}

void add_overshoot_trial(MovementAnalyzer& analyzer, const double time_offset) {
    analyzer.begin_trial(0.0, 0.0, 100.0, 0.0, time_offset);
    analyzer.add_sample(40.0, 2.0, time_offset + 0.01);
    analyzer.add_sample(90.0, -2.0, time_offset + 0.02);
    analyzer.add_sample(125.0, 3.0, time_offset + 0.03);
    analyzer.add_sample(112.0, -2.0, time_offset + 0.04);
    analyzer.add_sample(100.0, 0.0, time_offset + 0.05);
    assert(analyzer.end_trial(100.0, 0.0, time_offset + 0.06));
}

} // namespace

int main() {
    MovementAnalyzer straight;
    for (int i = 0; i < 12; ++i) {
        add_straight_trial(straight, static_cast<double>(i));
    }
    const auto straight_summary = straight.summary();
    const auto straight_curve = straight.recommend();
    assert(straight_summary.trial_count == 12);
    assert(straight_summary.path_efficiency > 0.99);
    assert(straight_curve.confidence > 0.9);
    assert(straight_curve.points.front().first == 0.0);
    assert(straight_curve.points.back().second == 1.0);

    MovementAnalyzer overshoot;
    for (int i = 0; i < 12; ++i) {
        add_overshoot_trial(overshoot, static_cast<double>(i));
    }
    const auto overshoot_summary = overshoot.summary();
    const auto overshoot_curve = overshoot.recommend();
    assert(overshoot_summary.overshoot_score > straight_summary.overshoot_score);
    assert(overshoot_curve.sensitivity_multiplier < straight_curve.sensitivity_multiplier);
    for (std::size_t i = 1; i < overshoot_curve.points.size(); ++i) {
        assert(overshoot_curve.points[i].first > overshoot_curve.points[i - 1].first);
        assert(overshoot_curve.points[i].second >= overshoot_curve.points[i - 1].second);
    }

    // A 1 kHz, 12-second trial must retain samples past the old 8192-point cap.
    MovementAnalyzer high_polling;
    high_polling.begin_trial(0.0, 0.0, 100.0, 0.0, 1.0);
    for (int i = 1; i <= 12'000; ++i) {
        const double x = 100.0 * static_cast<double>(i) / 12'000.0;
        const double y = (i % 2 == 0) ? 5.0 : -5.0;
        high_polling.add_sample(x, y, 1.0 + static_cast<double>(i) / 1000.0);
    }
    assert(high_polling.end_trial(100.0, 0.0, 13.1));
    assert(high_polling.summary().mean_speed_px_s > 9'000.0);

    std::cout << "movement analyzer tests passed\n";
    return 0;
}
