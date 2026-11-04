#include "fpscoach/movement_analyzer.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace fpscoach {
namespace {

constexpr double kEpsilon = 1e-9;
constexpr std::size_t kMaxSamplesPerTrial = 65'536;  // > 65 seconds at 1 kHz
constexpr double kSpeedReferenceQuantile = 0.95;
constexpr double kUniformAnchorWeight = 0.80;

double clamp01(const double value) {
    return std::clamp(value, 0.0, 1.0);
}

double distance(const double x0, const double y0, const double x1, const double y1) {
    return std::hypot(x1 - x0, y1 - y0);
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

// Same normalized chart-space tolerance as Python curve_sampling.KEY_TOLERANCE.
constexpr double kModelGraphTolerance = 0.005;
constexpr double kBalanceTieEpsilon = 1e-12;

double point_segment_distance(const double px, const double py,
                              const double x0, const double y0, const double x1, const double y1) {
    const double dx = x1 - x0, dy = y1 - y0;
    const double length_sq = dx * dx + dy * dy;
    if (length_sq == 0.0) return std::hypot(px - x0, py - y0);
    const double t = std::clamp(((px - x0) * dx + (py - y0) * dy) / length_sq, 0.0, 1.0);
    return std::hypot(px - (x0 + t * dx), py - (y0 + t * dy));
}

std::vector<std::size_t> balanced_indices(const std::vector<double>& xs, const std::vector<double>& ys) {
    // Shortest path over sampled model points. Among equally short paths,
    // prefer the one with the smaller maximum sampled chart-space error.
    // This is a visual sampling tolerance, not an analytic error guarantee.
    const std::size_t n = xs.size();
    std::vector<std::size_t> counts(n, n + 1), previous(n, n);
    std::vector<double> errors(n, std::numeric_limits<double>::infinity());
    counts[0] = 1;
    errors[0] = 0.0;
    for (std::size_t end = 1; end < n; ++end) {
        for (std::size_t start = 0; start < end; ++start) {
            const auto count = counts[start] + 1;
            if (count > counts[end]) continue;
            if (count == counts[end] && errors[start] >= errors[end] - kBalanceTieEpsilon) continue;
            double worst = errors[start];
            bool rejected = false;
            for (std::size_t i = start + 1; i < end; ++i) {
                worst = std::max(worst, point_segment_distance(xs[i], ys[i], xs[start], ys[start], xs[end], ys[end]));
                if (worst > kModelGraphTolerance ||
                    (count == counts[end] && worst >= errors[end] - kBalanceTieEpsilon)) {
                    rejected = true;
                    break;
                }
            }
            if (!rejected && worst <= kModelGraphTolerance &&
                (count < counts[end] || worst < errors[end] - kBalanceTieEpsilon)) {
                counts[end] = count;
                errors[end] = worst;
                previous[end] = start;
            }
        }
    }
    std::vector<std::size_t> indices{n - 1};
    while (indices.back() != 0) indices.push_back(previous[indices.back()]);
    std::reverse(indices.begin(), indices.end());
    return indices;
}

} // namespace

void MovementAnalyzer::reset() {
    std::scoped_lock lock(mutex_);
    active_ = false;
    current_ = {};
    recent_trials_.clear();
    total_trial_count_ = 0;
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

void MovementAnalyzer::set_target(const double x, const double y) {
    std::scoped_lock lock(mutex_);
    if (active_ && std::isfinite(x) && std::isfinite(y)) {
        current_.target.x = x;
        current_.target.y = y;
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
    // Accept a real single observed segment (start plus one movement). Sparse
    // paths do not prove that no intermediate corrections occurred.
    if (current_.samples.size() < 2 || direct_distance < 8.0 ||
        current_.samples.back().t <= current_.samples.front().t) {
        current_ = {};
        return false;
    }

    WindowTrial accepted{};
    accepted.metrics = calculate_metrics(current_);
    accepted.captured_sample_count = current_.samples.size() - 1;
    for (std::size_t i = 1; i < current_.samples.size(); ++i) {
        const auto& before = current_.samples[i - 1];
        const auto& after = current_.samples[i];
        const double elapsed = after.t - before.t;
        if (elapsed <= 0.0) {
            continue;
        }
        const double speed = distance(before.x, before.y, after.x, after.y) / elapsed;
        if (std::isfinite(speed) && speed > 0.0) {
            accepted.speeds_px_s.push_back(speed);
        }
    }
    recent_trials_.push_back(std::move(accepted));
    if (recent_trials_.size() > kCalibrationWindowTrials) {
        recent_trials_.pop_front();
    }
    ++total_trial_count_;
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
    result.trial_count = total_trial_count_;
    result.window_trial_count = recent_trials_.size();
    if (recent_trials_.empty()) {
        return result;
    }

    for (const auto& entry : recent_trials_) {
        const auto& trial = entry.metrics;
        result.path_efficiency += trial.path_efficiency;
        result.overshoot_score += trial.overshoot;
        result.underreach_score += trial.underreach;
        result.correction_score += trial.corrections;
        result.tremor_score += trial.tremor;
        result.mean_endpoint_error_px += trial.endpoint_error_px;
        result.mean_duration_ms += trial.duration_ms;
        result.mean_speed_px_s += trial.mean_speed_px_s;
    }

    const double count = static_cast<double>(recent_trials_.size());
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
    recommendation.window_trial_count = aggregate.window_trial_count;

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
    std::vector<double> measured_speeds;
    for (const auto& entry : recent_trials_) {
        recommendation.captured_sample_count += entry.captured_sample_count;
        measured_speeds.insert(measured_speeds.end(), entry.speeds_px_s.begin(), entry.speeds_px_s.end());
    }
    std::sort(measured_speeds.begin(), measured_speeds.end());
    if (!measured_speeds.empty()) {
        // Sample the existing model throughout the domain and include
        // additional speed-distribution candidates. These are model anchors,
        // not raw cursor samples.
        const auto reference_index = static_cast<std::size_t>(
            std::ceil(kSpeedReferenceQuantile * static_cast<double>(measured_speeds.size()))) - 1;
        const double speed_scale = measured_speeds[reference_index];
        recommendation.speed_reference_px_s = speed_scale;
        recommendation.clipped_speed_count = static_cast<std::size_t>(std::count_if(
            measured_speeds.begin(), measured_speeds.end(),
            [speed_scale](double speed) { return speed > speed_scale; }));
        std::vector<double> xs;
        for (int i = 0; i <= 128; ++i) xs.push_back(static_cast<double>(i) / 128.0);
        for (int i = 1; i < 32; ++i) {
            const double fraction = static_cast<double>(i) / 32.0;
            const double observed_x = std::clamp(speed_quantile(measured_speeds, fraction) / speed_scale, 0.0, 1.0);
            xs.push_back(kUniformAnchorWeight * fraction + (1.0 - kUniformAnchorWeight) * observed_x);
        }
        std::sort(xs.begin(), xs.end());
        xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
        std::vector<double> ys;
        for (const double x : xs) {
            ys.push_back(std::pow(x, gamma) * (1.0 - 0.12 * aggregate.tremor_score * (1.0 - x)));
        }
        for (const auto index : balanced_indices(xs, ys)) {
            recommendation.points.emplace_back(xs[index], ys[index]);
        }
    }

    double variance = 0.0;
    for (const auto& entry : recent_trials_) {
        const double delta = entry.metrics.overshoot - aggregate.overshoot_score;
        variance += delta * delta;
    }
    if (!recent_trials_.empty()) {
        variance /= static_cast<double>(recent_trials_.size());
    }
    const double consistency = 1.0 - clamp01(std::sqrt(variance) * 1.7);
    const double sample_confidence = std::min(
        1.0, static_cast<double>(recent_trials_.size()) / static_cast<double>(kCalibrationWindowTrials));
    const double density = recent_trials_.empty() ? 0.0 : std::min(
        1.0, static_cast<double>(recommendation.captured_sample_count) /
             (2.0 * static_cast<double>(recent_trials_.size())));
    recommendation.confidence = sample_confidence * (0.55 + 0.45 * consistency) * density;

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
    if (recent_trials_.size() < kCalibrationWindowTrials) {
        recommendation.reasons.emplace_back("Complete at least 8-12 trials before treating this as a stable recommendation.");
    }
    if (!recent_trials_.empty() && density < 1.0) {
        recommendation.reasons.emplace_back("Sparse movement observations cannot resolve intermediate corrections; repeat with more samples for a clearer estimate.");
    }

    return recommendation;
}

bool MovementAnalyzer::trial_active() const {
    std::scoped_lock lock(mutex_);
    return active_;
}

} // namespace fpscoach
