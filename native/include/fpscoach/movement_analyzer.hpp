#pragma once

#include <cstddef>
#include <deque>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace fpscoach {

inline constexpr std::size_t kCalibrationWindowTrials = 8;
inline constexpr std::size_t kCurveDistributionVersion = 4;

struct MovementSummary {
    std::size_t trial_count{};  // all accepted trials this session
    std::size_t window_trial_count{};
    std::size_t window_capacity{kCalibrationWindowTrials};
    std::size_t curve_distribution_version{kCurveDistributionVersion};
    double path_efficiency{};
    double overshoot_score{};
    double underreach_score{};
    double correction_score{};
    double tremor_score{};
    double mean_endpoint_error_px{};
    double mean_duration_ms{};
    double mean_speed_px_s{};
};

struct CurveRecommendation {
    std::vector<std::pair<double, double>> points;
    std::size_t captured_sample_count{};  // samples in the current rolling window
    double speed_reference_px_s{};
    std::size_t clipped_speed_count{};
    std::size_t window_trial_count{};
    std::size_t window_capacity{kCalibrationWindowTrials};
    double sensitivity_multiplier{1.0};
    double confidence{};
    std::string title;
    std::vector<std::string> reasons;
};

// An explainable, fully local movement model. It consumes calibration trials and
// produces a monotonic input-speed -> output-speed curve. It never sends input.
class MovementAnalyzer {
public:
    void reset();

    // Coordinates must use the same coordinate space throughout a trial.
    void begin_trial(double start_x,
                     double start_y,
                     double target_x,
                     double target_y,
                     double timestamp_seconds);
    void add_sample(double x, double y, double timestamp_seconds);
    void set_target(double x, double y);  // actual target position at click time
    bool end_trial(double x, double y, double timestamp_seconds);

    [[nodiscard]] MovementSummary summary() const;
    [[nodiscard]] CurveRecommendation recommend() const;
    [[nodiscard]] bool trial_active() const;

private:
    struct Sample {
        double x{};
        double y{};
        double t{};
    };

    struct Trial {
        Sample start;
        Sample target;
        std::vector<Sample> samples;
    };

    struct TrialMetrics {
        double path_efficiency{};
        double overshoot{};
        double underreach{};
        double corrections{};
        double tremor{};
        double endpoint_error_px{};
        double duration_ms{};
        double mean_speed_px_s{};
    };

    struct WindowTrial {
        TrialMetrics metrics;
        std::vector<double> speeds_px_s;
        std::size_t captured_sample_count{};
    };

    static TrialMetrics calculate_metrics(const Trial& trial);
    MovementSummary summary_unlocked() const;

    mutable std::mutex mutex_;
    bool active_{false};
    Trial current_;
    std::deque<WindowTrial> recent_trials_;
    std::size_t total_trial_count_{};
};

} // namespace fpscoach
