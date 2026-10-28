#include "fpscoach/movement_analyzer.hpp"

#include <algorithm>
#include <cmath>

namespace fpscoach {
namespace {

constexpr double kEpsilon = 1e-9;
constexpr std::size_t kMaxSamplesPerTrial = 65'536;  // > 65 seconds at 1 kHz

double clamp01(const double value) {
    return std::clamp(value, 0.0, 1.0);
}

double distance(const double x0, const double y0, const double x1, const double y1) {
    return std::hypot(x1 - x0, y1 - y0);
}

std::size_t adaptive_point_count(const std::size_t sample_count) {
    constexpr std::size_t kMinimumPoints = 2;
    constexpr std::size_t kMaximumPoints = 32;
    const auto requested = static_cast<std::size_t>(
                               std::ceil(std::log2(static_cast<double>(sample_count) + 1.0))) +
                           1;
    return std::clamp(requested, kMinimumPoints, kMaximumPoints);
}

double speed_quantile(const std::vector<double>& sorted_speeds, const double fraction) {
    if (sorted_speeds.empty()) {
        return 0.0;
    }
    const double position = fraction * static_cast<double>(sorted_speeds.size() - 1);
    const auto lower = static_cast<std::size_t>(std::floor(position));
    const auto upper = static_cast<std::size_t>(std::ceil(position));
    const double weight = position - static_cast<double>(lower);
    return sorted_speeds[lower] * (1.0 - weight) + sorted_speeds[upper] * weight;
}

} // namespace

void MovementAnalyzer::reset() {
    std::scoped_lock lock(mutex_);
    active_ = false;
    current_ = {};
    completed_.clear();
    captured_speeds_px_s_.clear();
    captured_sample_count_ = 0;
}

void MovementAnalyzer::begin_trial(const double start_x,
                                   const double start_y,
                                   const double target_x,
                                   const double target_y,
                                   const double timestamp_seconds) {
    std::scoped_lock lock(mutex_);
    current_ = {};
    current_.start = {start_x, start_y, timestamp_seconds};
    current_.target = {target_x, target_y, timestamp_seconds};
    current_.samples.push_back(current_.start);
    active_ = true;
}

void MovementAnalyzer::add_sample(const double x,
                                  const double y,
                                  const double timestamp_seconds) {
    std::scoped_lock lock(mutex_);
    if (!active_ || !std::isfinite(timestamp_seconds)) {
        return;
    }

    const auto& previous = current_.samples.back();
    if (timestamp_seconds <= previous.t) {
        return;
    }

    // Ignore stationary timer ticks; they add no movement information and can
    // otherwise dominate slower systems.
    if (distance(previous.x, previous.y, x, y) < 0.05) {
        return;
    }

    // Bound memory if a caller leaves a calibration trial running, while retaining
    // more than a minute of samples at the 1 kHz calibration setting.
    if (current_.samples.size() < kMaxSamplesPerTrial) {
        current_.samples.push_back({x, y, timestamp_seconds});
    }
}

bool MovementAnalyzer::end_trial(const double x,
                                 const double y,
                                 const double timestamp_seconds) {
    std::scoped_lock lock(mutex_);
    if (!active_) {
        return false;
    }

    const auto& previous = current_.samples.back();
    if (timestamp_seconds > previous.t && distance(previous.x, previous.y, x, y) >= 0.05) {
        const Sample endpoint{x, y, timestamp_seconds};
        if (current_.samples.size() < kMaxSamplesPerTrial) {
            current_.samples.push_back(endpoint);
        } else {
            current_.samples.back() = endpoint;
        }
    }

    active_ = false;
    const double direct_distance = distance(current_.start.x,
                                            current_.start.y,
                                            current_.target.x,
                                            current_.target.y);
    if (current_.samples.size() < 3 || direct_distance < 8.0 ||
        current_.samples.back().t <= current_.samples.front().t) {
        current_ = {};
        return false;
    }

    completed_.push_back(calculate_metrics(current_));
    for (std::size_t i = 1; i < current_.samples.size(); ++i) {
        const auto& before = current_.samples[i - 1];
        const auto& after = current_.samples[i];
        const double elapsed = after.t - before.t;
        if (elapsed <= 0.0) {
            continue;
        }
        const double speed = distance(before.x, before.y, after.x, after.y) / elapsed;
        if (std::isfinite(speed) && speed > 0.0) {
            captured_speeds_px_s_.push_back(speed);
        }
    }
    captured_sample_count_ += current_.samples.size() - 1;
    current_ = {};
    return true;
}

MovementAnalyzer::TrialMetrics MovementAnalyzer::calculate_metrics(const Trial& trial) {
    TrialMetrics result{};
    const auto& start = trial.start;
    const auto& target = trial.target;
    const double dx = target.x - start.x;
    const double dy = target.y - start.y;
    const double direct = std::max(distance(start.x, start.y, target.x, target.y), kEpsilon);
    const double ux = dx / direct;
    const double uy = dy / direct;
    const double px = -uy;
    const double py = ux;

    double path_length = 0.0;
    double max_projection = 0.0;
    double perpendicular_square_sum = 0.0;
    int backward_segments = 0;
    int side_reversals = 0;
    int previous_side = 0;
    const double movement_threshold = std::max(0.75, direct * 0.006);

    for (std::size_t i = 0; i < trial.samples.size(); ++i) {
        const auto& sample = trial.samples[i];
        const double relative_x = sample.x - start.x;
        const double relative_y = sample.y - start.y;
        const double projection = relative_x * ux + relative_y * uy;
        const double perpendicular = relative_x * px + relative_y * py;
        max_projection = std::max(max_projection, projection);
        perpendicular_square_sum += perpendicular * perpendicular;

        int side = 0;
        if (perpendicular > movement_threshold) {
            side = 1;
        } else if (perpendicular < -movement_threshold) {
            side = -1;
        }
        if (side != 0) {
            if (previous_side != 0 && side != previous_side) {
                ++side_reversals;
            }
            previous_side = side;
        }

        if (i == 0) {
            continue;
        }
        const auto& before = trial.samples[i - 1];
        path_length += distance(before.x, before.y, sample.x, sample.y);
        const double segment_projection = (sample.x - before.x) * ux + (sample.y - before.y) * uy;
        if (segment_projection < -movement_threshold) {
            ++backward_segments;
        }
    }

    const auto& end = trial.samples.back();
    const double endpoint_projection = ((end.x - start.x) * ux + (end.y - start.y) * uy) / direct;
    const double endpoint_error = distance(end.x, end.y, target.x, target.y);
    const double rms_perpendicular = std::sqrt(perpendicular_square_sum /
                                               static_cast<double>(trial.samples.size()));
    const double duration = std::max(end.t - trial.samples.front().t, kEpsilon);

    result.path_efficiency = clamp01(direct / std::max(path_length, direct));
    result.overshoot = clamp01(((max_projection / direct) - 1.0) * 3.0);
    result.underreach = clamp01(std::max(0.0, 1.0 - endpoint_projection) * 2.5);
    result.corrections = clamp01((static_cast<double>(backward_segments) +
                                  0.5 * static_cast<double>(side_reversals)) /
                                 6.0);
    result.tremor = clamp01(0.55 * (rms_perpendicular / std::max(direct * 0.08, 1.0)) +
                            0.45 * (static_cast<double>(side_reversals) / 8.0));
    result.endpoint_error_px = endpoint_error;
    result.duration_ms = duration * 1000.0;
    result.mean_speed_px_s = path_length / duration;
    return result;
}

MovementSummary MovementAnalyzer::summary_unlocked() const {
    MovementSummary result{};
    result.trial_count = completed_.size();
    if (completed_.empty()) {
        return result;
    }

    for (const auto& trial : completed_) {
        result.path_efficiency += trial.path_efficiency;
        result.overshoot_score += trial.overshoot;
        result.underreach_score += trial.underreach;
        result.correction_score += trial.corrections;
        result.tremor_score += trial.tremor;
        result.mean_endpoint_error_px += trial.endpoint_error_px;
        result.mean_duration_ms += trial.duration_ms;
        result.mean_speed_px_s += trial.mean_speed_px_s;
    }

    const double count = static_cast<double>(completed_.size());
    result.path_efficiency /= count;
    result.overshoot_score /= count;
    result.underreach_score /= count;
    result.correction_score /= count;
    result.tremor_score /= count;
    result.mean_endpoint_error_px /= count;
    result.mean_duration_ms /= count;
    result.mean_speed_px_s /= count;
    return result;
}

MovementSummary MovementAnalyzer::summary() const {
    std::scoped_lock lock(mutex_);
    return summary_unlocked();
}

CurveRecommendation MovementAnalyzer::recommend() const {
    std::scoped_lock lock(mutex_);
    const auto aggregate = summary_unlocked();
    CurveRecommendation recommendation{};

    const double gamma = std::clamp(1.0 +
                                        0.55 * aggregate.overshoot_score +
                                        0.30 * aggregate.tremor_score +
                                        0.20 * aggregate.correction_score -
                                        0.35 * aggregate.underreach_score,
                                    0.72,
                                    1.50);
    const double sensitivity = std::clamp(1.0 -
                                              0.12 * aggregate.overshoot_score -
                                              0.05 * aggregate.correction_score +
                                              0.10 * aggregate.underreach_score,
                                          0.82,
                                          1.12);
    recommendation.sensitivity_multiplier = sensitivity;
    recommendation.captured_sample_count = captured_sample_count_;

    auto measured_speeds = captured_speeds_px_s_;
    std::sort(measured_speeds.begin(), measured_speeds.end());
    if (!measured_speeds.empty()) {
        // The count grows with captured samples (capped at 32 for readability), while
        // x positions come from empirical speed quantiles. The graph therefore reflects
        // this user's movement rather than a fixed seven-anchor template.
        const auto point_count = adaptive_point_count(measured_speeds.size());
        const double speed_scale = measured_speeds.back();
        recommendation.points.emplace_back(0.0, 0.0);
        double previous_x = 0.0;
        for (std::size_t index = 1; index + 1 < point_count; ++index) {
            const double fraction = static_cast<double>(index) /
                                    static_cast<double>(point_count - 1);
            const double x = std::clamp(speed_quantile(measured_speeds, fraction) / speed_scale,
                                        0.0,
                                        1.0);
            // Repeated speeds add no horizontal detail; omit duplicate control points.
            if (x <= previous_x + 1e-9 || x >= 1.0 - 1e-9) {
                continue;
            }
            double y = std::pow(x, gamma);
            // Apply a small low-speed damping term only when repeated lateral
            // oscillation was measured. The term fades out at high speed.
            y *= 1.0 - 0.12 * aggregate.tremor_score * (1.0 - x);
            y = std::clamp(y, recommendation.points.back().second, 1.0);
            recommendation.points.emplace_back(x, y);
            previous_x = x;
        }
        recommendation.points.emplace_back(1.0, 1.0);
    }

    double variance = 0.0;
    for (const auto& trial : completed_) {
        const double delta = trial.overshoot - aggregate.overshoot_score;
        variance += delta * delta;
    }
    if (!completed_.empty()) {
        variance /= static_cast<double>(completed_.size());
    }
    const double consistency = 1.0 - clamp01(std::sqrt(variance) * 1.7);
    const double sample_confidence = std::min(1.0, static_cast<double>(completed_.size()) / 12.0);
    recommendation.confidence = sample_confidence * (0.55 + 0.45 * consistency);

    if (aggregate.overshoot_score > 0.18 || aggregate.correction_score > 0.28) {
        recommendation.title = "Add control through the middle of the curve";
        recommendation.reasons.emplace_back("Repeated overshoot or reverse corrections suggest reducing mid-speed response.");
    } else if (aggregate.underreach_score > 0.20) {
        recommendation.title = "Increase low-to-mid speed response slightly";
        recommendation.reasons.emplace_back("The pointer often stopped short of the target during calibration.");
    } else {
        recommendation.title = "Keep a near-linear response";
        recommendation.reasons.emplace_back("The measured paths do not show a strong need for acceleration.");
    }

    if (aggregate.tremor_score > 0.20) {
        recommendation.reasons.emplace_back("Low-speed response is softened to reduce the effect of small oscillations.");
    }
    if (aggregate.path_efficiency > 0.90) {
        recommendation.reasons.emplace_back("Path efficiency is high, so the recommendation stays close to the current feel.");
    }
    if (completed_.size() < 8) {
        recommendation.reasons.emplace_back("Complete at least 8-12 trials before treating this as a stable recommendation.");
    }

    return recommendation;
}

bool MovementAnalyzer::trial_active() const {
    std::scoped_lock lock(mutex_);
    return active_;
}

} // namespace fpscoach
