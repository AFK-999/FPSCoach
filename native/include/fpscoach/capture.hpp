#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace fpscoach {

struct CaptureRegion {
    // Negative x/y centers the requested width/height on the output.
    // Non-positive width/height captures the full output.
    int x{-1};
    int y{-1};
    int width{640};
    int height{360};
};

struct CaptureConfig {
    std::uint32_t adapter_index{};
    std::uint32_t output_index{};
    CaptureRegion region{};
    std::uint32_t target_fps{60};
};

struct OutputInfo {
    std::uint32_t adapter_index{};
    std::uint32_t output_index{};
    std::string adapter_name;
    std::string output_name;
    int left{};
    int top{};
    int width{};
    int height{};
    bool attached_to_desktop{};
};

struct CapturedFrame {
    std::uint64_t sequence{};
    std::uint64_t timestamp_us{};
    int width{};
    int height{};
    int stride{};
    bool pointer_visible{};
    int pointer_x{};
    int pointer_y{};
    // Tightly packed BGRA8 pixels. Only the latest frame is retained.
    std::vector<std::uint8_t> pixels;
};

struct CaptureStats {
    bool running{};
    double capture_fps{};
    std::uint64_t frames_captured{};
    std::uint64_t acquire_timeouts{};
    std::uint64_t access_lost_count{};
    std::uint64_t last_frame_age_ms{};
    int capture_width{};
    int capture_height{};
    std::string adapter_name;
    std::string last_error;
};

// Desktop Duplication runs on a native worker so Python never sits in the
// capture hot path. The device is created on the adapter that owns the output,
// which includes integrated Intel/AMD GPUs.
class DesktopDuplicationCapture {
public:
    DesktopDuplicationCapture();
    ~DesktopDuplicationCapture();

    DesktopDuplicationCapture(const DesktopDuplicationCapture&) = delete;
    DesktopDuplicationCapture& operator=(const DesktopDuplicationCapture&) = delete;

    void start(const CaptureConfig& config);
    void stop();
    [[nodiscard]] bool running() const;
    // The newest frame, shared rather than copied. Frames are never changed once published.
    [[nodiscard]] std::shared_ptr<const CapturedFrame> latest_frame() const;
    // Number of the newest frame (0 before the first). Copies no pixels.
    [[nodiscard]] std::uint64_t latest_sequence() const;
    [[nodiscard]] CaptureStats stats() const;

    static std::vector<OutputInfo> enumerate_outputs();

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace fpscoach
