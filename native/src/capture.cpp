#include "fpscoach/capture.hpp"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

// Window capture uses Windows Graphics Capture, which needs the C++/WinRT headers and Windows 10 SDK
// 1903 or later. Builds without them (Linux test builds included) compile the screen-area path only.
#if defined(_WIN32) && defined(__has_include)
#  if __has_include(<winrt/Windows.Graphics.Capture.h>) && __has_include(<windows.graphics.capture.interop.h>) && \
      __has_include(<windows.graphics.directx.direct3d11.interop.h>)
#    define FPSCOACH_HAS_WGC 1
#  endif
#endif
#ifndef FPSCOACH_HAS_WGC
#  define FPSCOACH_HAS_WGC 0
#endif
#if FPSCOACH_HAS_WGC
#  include <winrt/Windows.Foundation.h>
#  include <winrt/Windows.Graphics.h>
#  include <winrt/Windows.Graphics.Capture.h>
#  include <winrt/Windows.Graphics.DirectX.h>
#  include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#  include <windows.graphics.capture.interop.h>
#  include <windows.graphics.directx.direct3d11.interop.h>
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <string>
#include <cstring>
#include <iterator>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace fpscoach {
namespace {

using Microsoft::WRL::ComPtr;
using Clock = std::chrono::steady_clock;

std::uint64_t now_us() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(Clock::now().time_since_epoch()).count());
}

std::string utf8(const wchar_t* value) {
    if (value == nullptr || *value == L'\0') {
        return {};
    }
    const int needed = WideCharToMultiByte(CP_UTF8, 0, value, -1, nullptr, 0, nullptr, nullptr);
    if (needed <= 1) {
        return {};
    }
    std::string result(static_cast<std::size_t>(needed), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value, -1, result.data(), needed, nullptr, nullptr);
    result.resize(static_cast<std::size_t>(needed - 1));
    return result;
}

std::string hr_message(const char* operation, const HRESULT hr) {
    std::ostringstream stream;
    stream << operation << " failed (HRESULT 0x" << std::hex << static_cast<unsigned long>(hr) << ')';
    return stream.str();
}

struct DuplicationResources {
    ComPtr<IDXGIAdapter1> adapter;
    ComPtr<IDXGIOutput> output;
    ComPtr<IDXGIOutput1> output1;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<IDXGIOutputDuplication> duplication;
    ComPtr<ID3D11Texture2D> staging;
    DXGI_OUTPUT_DESC output_desc{};
    D3D11_TEXTURE2D_DESC staging_desc{};
    CaptureRegion resolved_region{};
    std::string adapter_name;

    bool initialize(const CaptureConfig& config, std::string& error) {
        ComPtr<IDXGIFactory1> factory;
        HRESULT hr = CreateDXGIFactory1(IID_PPV_ARGS(&factory));
        if (FAILED(hr)) {
            error = hr_message("CreateDXGIFactory1", hr);
            return false;
        }

        hr = factory->EnumAdapters1(config.adapter_index, &adapter);
        if (FAILED(hr)) {
            error = "The selected graphics adapter is no longer available.";
            return false;
        }

        DXGI_ADAPTER_DESC1 adapter_desc{};
        adapter->GetDesc1(&adapter_desc);
        adapter_name = utf8(adapter_desc.Description);

        hr = adapter->EnumOutputs(config.output_index, &output);
        if (FAILED(hr)) {
            error = "The selected display output is no longer available.";
            return false;
        }
        output->GetDesc(&output_desc);

        hr = output.As(&output1);
        if (FAILED(hr)) {
            error = "This display driver does not support Desktop Duplication (DXGI 1.2).";
            return false;
        }

        constexpr D3D_FEATURE_LEVEL feature_levels[] = {
            D3D_FEATURE_LEVEL_11_1,
            D3D_FEATURE_LEVEL_11_0,
            D3D_FEATURE_LEVEL_10_1,
            D3D_FEATURE_LEVEL_10_0,
        };
        D3D_FEATURE_LEVEL selected_level{};
        hr = D3D11CreateDevice(adapter.Get(),
                               D3D_DRIVER_TYPE_UNKNOWN,
                               nullptr,
                               D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                               feature_levels,
                               static_cast<UINT>(std::size(feature_levels)),
                               D3D11_SDK_VERSION,
                               &device,
                               &selected_level,
                               &context);
        if (hr == E_INVALIDARG) {
            hr = D3D11CreateDevice(adapter.Get(),
                                   D3D_DRIVER_TYPE_UNKNOWN,
                                   nullptr,
                                   D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                   feature_levels + 1,
                                   static_cast<UINT>(std::size(feature_levels) - 1),
                                   D3D11_SDK_VERSION,
                                   &device,
                                   &selected_level,
                                   &context);
        }
        if (FAILED(hr)) {
            error = hr_message("D3D11CreateDevice on the selected output adapter", hr);
            return false;
        }

        hr = output1->DuplicateOutput(device.Get(), &duplication);
        if (FAILED(hr)) {
            if (hr == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE) {
                error = "Desktop Duplication is busy (Windows allows only a limited number of clients).";
            } else if (hr == E_ACCESSDENIED) {
                error = "Desktop Duplication cannot capture the secure desktop.";
            } else {
                error = hr_message("IDXGIOutput1::DuplicateOutput", hr);
            }
            return false;
        }

        const int output_width = output_desc.DesktopCoordinates.right - output_desc.DesktopCoordinates.left;
        const int output_height = output_desc.DesktopCoordinates.bottom - output_desc.DesktopCoordinates.top;
        resolved_region.width = config.region.width <= 0
                                    ? output_width
                                    : std::min(config.region.width, output_width);
        resolved_region.height = config.region.height <= 0
                                     ? output_height
                                     : std::min(config.region.height, output_height);
        resolved_region.x = config.region.x < 0
                                ? (output_width - resolved_region.width) / 2
                                : std::clamp(config.region.x, 0, output_width - resolved_region.width);
        resolved_region.y = config.region.y < 0
                                ? (output_height - resolved_region.height) / 2
                                : std::clamp(config.region.y, 0, output_height - resolved_region.height);
        return true;
    }

    bool ensure_staging(const D3D11_TEXTURE2D_DESC& source_desc, std::string& error) {
        if (staging && staging_desc.Width == static_cast<UINT>(resolved_region.width) &&
            staging_desc.Height == static_cast<UINT>(resolved_region.height) &&
            staging_desc.Format == source_desc.Format) {
            return true;
        }

        staging.Reset();
        staging_desc = source_desc;
        staging_desc.Width = static_cast<UINT>(resolved_region.width);
        staging_desc.Height = static_cast<UINT>(resolved_region.height);
        staging_desc.MipLevels = 1;
        staging_desc.ArraySize = 1;
        staging_desc.SampleDesc.Count = 1;
        staging_desc.SampleDesc.Quality = 0;
        staging_desc.Usage = D3D11_USAGE_STAGING;
        staging_desc.BindFlags = 0;
        staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        staging_desc.MiscFlags = 0;

        const HRESULT hr = device->CreateTexture2D(&staging_desc, nullptr, &staging);
        if (FAILED(hr)) {
            error = hr_message("CreateTexture2D for the staging region", hr);
            return false;
        }
        return true;
    }

    enum class AcquireResult { frame, timeout, access_lost, failure };

    AcquireResult acquire(CapturedFrame& frame, const UINT timeout_ms, std::string& error) {
        DXGI_OUTDUPL_FRAME_INFO frame_info{};
        ComPtr<IDXGIResource> resource;
        HRESULT hr = duplication->AcquireNextFrame(timeout_ms, &frame_info, &resource);
        if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
            return AcquireResult::timeout;
        }
        if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_DEVICE_REMOVED ||
            hr == DXGI_ERROR_DEVICE_RESET) {
            return AcquireResult::access_lost;
        }
        if (FAILED(hr)) {
            error = hr_message("AcquireNextFrame", hr);
            return AcquireResult::failure;
        }

        // Taken as soon as the desktop frame is handed over, before the copy and any pacing.
        frame.arrival_us = now_us();

        struct FrameReleaser {
            IDXGIOutputDuplication* value;
            ~FrameReleaser() { value->ReleaseFrame(); }
        } release{duplication.Get()};

        ComPtr<ID3D11Texture2D> source;
        hr = resource.As(&source);
        if (FAILED(hr)) {
            error = hr_message("Query ID3D11Texture2D", hr);
            return AcquireResult::failure;
        }

        D3D11_TEXTURE2D_DESC source_desc{};
        source->GetDesc(&source_desc);
        if (!ensure_staging(source_desc, error)) {
            return AcquireResult::failure;
        }

        const D3D11_BOX source_box{
            static_cast<UINT>(resolved_region.x),
            static_cast<UINT>(resolved_region.y),
            0,
            static_cast<UINT>(resolved_region.x + resolved_region.width),
            static_cast<UINT>(resolved_region.y + resolved_region.height),
            1,
        };
        context->CopySubresourceRegion(staging.Get(), 0, 0, 0, 0, source.Get(), 0, &source_box);

        D3D11_MAPPED_SUBRESOURCE mapped{};
        hr = context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped);
        if (hr == DXGI_ERROR_DEVICE_REMOVED || hr == DXGI_ERROR_DEVICE_RESET) {
            return AcquireResult::access_lost;
        }
        if (FAILED(hr)) {
            error = hr_message("Map staging texture", hr);
            return AcquireResult::failure;
        }

        struct TextureUnmapper {
            ID3D11DeviceContext* context;
            ID3D11Texture2D* texture;
            ~TextureUnmapper() { context->Unmap(texture, 0); }
        } unmap{context.Get(), staging.Get()};

        frame.timestamp_us = now_us();
        frame.width = resolved_region.width;
        frame.height = resolved_region.height;
        frame.stride = frame.width * 4;
        frame.pointer_visible = frame_info.PointerPosition.Visible != 0;
        frame.pointer_x = frame_info.PointerPosition.Position.x - output_desc.DesktopCoordinates.left -
                          resolved_region.x;
        frame.pointer_y = frame_info.PointerPosition.Position.y - output_desc.DesktopCoordinates.top -
                          resolved_region.y;
        frame.pixels.resize(static_cast<std::size_t>(frame.stride) *
                            static_cast<std::size_t>(frame.height));

        const auto* source_bytes = static_cast<const std::uint8_t*>(mapped.pData);
        if (mapped.RowPitch == static_cast<UINT>(frame.stride)) {
            // Rows are packed already: one copy for the whole frame.
            std::memcpy(frame.pixels.data(), source_bytes, frame.pixels.size());
        } else {
            for (int row = 0; row < frame.height; ++row) {
                std::memcpy(frame.pixels.data() + static_cast<std::size_t>(row * frame.stride),
                            source_bytes + static_cast<std::size_t>(row) * mapped.RowPitch,
                            static_cast<std::size_t>(frame.stride));
            }
        }
        return AcquireResult::frame;
    }
};

} // namespace

namespace {

constexpr const char* kWindowUnavailableNotice =
    "Window capture is not available in this build. Showing the screen area behind the window instead, "
    "so windows on top of it will appear.";

} // namespace

class DesktopDuplicationCapture::Impl {
public:
    ~Impl() { stop(); }

    void start(const CaptureConfig& config) {
        stop();
        reset_for(config, 0);
        set_mode(config_.region.width <= 0 && config_.region.height <= 0 ? "output" : "region");
        start_worker(false);
    }

    void start_window(const std::uint64_t hwnd, const CaptureConfig& fallback) {
        stop();
        reset_for(fallback, hwnd);
        if (FPSCOACH_HAS_WGC) {
            set_mode("window");
        } else {
            // Decided here, not in the worker, so the first status read is already correct.
            set_mode("region");
            set_notice(kWindowUnavailableNotice);
        }
        start_worker(FPSCOACH_HAS_WGC != 0);
    }

    void stop() {
        {
            std::scoped_lock lock(gate_mutex_);
            running_ = false;
        }
        gate_cv_.notify_all();
        if (worker_.joinable()) {
            worker_.join();
        }
    }

    [[nodiscard]] bool running() const { return running_; }

    [[nodiscard]] std::shared_ptr<const CapturedFrame> latest_frame() const {
        std::scoped_lock lock(frame_mutex_);
        return latest_;
    }

    [[nodiscard]] std::uint64_t latest_sequence() const {
        std::scoped_lock lock(frame_mutex_);
        return latest_ ? latest_->sequence : 0;
    }

    [[nodiscard]] CaptureStats stats() const {
        CaptureStats value{};
        value.running = running_;
        value.frames_captured = frames_captured_;
        value.acquire_timeouts = acquire_timeouts_;
        value.access_lost_count = access_lost_count_;
        const std::uint64_t last = last_frame_timestamp_us_;
        value.last_frame_age_ms = last == 0 ? 0 : (now_us() - last) / 1000;
        {
            std::scoped_lock lock(state_mutex_);
            value.capture_fps = capture_fps_;
            value.capture_width = capture_width_;
            value.capture_height = capture_height_;
            value.adapter_name = adapter_name_;
            value.last_error = last_error_;
            value.capture_mode = capture_mode_;
            value.notice = notice_;
        }
        return value;
    }

    CaptureConfig config_{};
    std::atomic<bool> running_{false};
    std::thread worker_;
    std::uint64_t hwnd_{};

    mutable std::mutex frame_mutex_;
    std::shared_ptr<CapturedFrame> latest_;
    std::vector<std::uint8_t> recycled_pixels_;  // worker thread only

    mutable std::mutex state_mutex_;
    double capture_fps_{};
    int capture_width_{};
    int capture_height_{};
    std::string adapter_name_;
    std::string last_error_;
    std::string capture_mode_;
    std::string notice_;  // set by start paths; the worker never clears it

    std::atomic<std::uint64_t> frames_captured_{0};
    std::atomic<std::uint64_t> acquire_timeouts_{0};
    std::atomic<std::uint64_t> access_lost_count_{0};
    std::atomic<std::uint64_t> last_frame_timestamp_us_{0};

    // Worker-only pacing and bookkeeping.
    std::uint64_t sequence_{0};
    Clock::time_point last_copy_{};
    Clock::time_point fps_window_start_{};
    std::uint64_t fps_window_frames_{0};

    // Window path: the newest arrived frame waits here until the worker takes it.
    std::mutex gate_mutex_;
    std::condition_variable gate_cv_;
    std::uint64_t pending_arrival_us_{0};  // guarded by gate_mutex_
    std::atomic<int> window_pool_width_{0};
    std::atomic<int> window_pool_height_{0};

    ComPtr<ID3D11Device> window_device_;
    ComPtr<ID3D11DeviceContext> window_context_;
    ComPtr<ID3D11Texture2D> window_staging_;
    D3D11_TEXTURE2D_DESC window_staging_desc_{};

#if FPSCOACH_HAS_WGC
    winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice window_device_rt_{nullptr};
    winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool window_pool_{nullptr};
    winrt::Windows::Graphics::Capture::GraphicsCaptureSession window_session_{nullptr};
    winrt::Windows::Graphics::Capture::Direct3D11CaptureFrame pending_{nullptr};  // guarded by gate_mutex_
    winrt::event_token window_frame_token_{};
#endif

private:
    void reset_for(const CaptureConfig& config, const std::uint64_t hwnd) {
        config_ = config;
        config_.target_fps = std::clamp(config_.target_fps, 1U, 240U);
        hwnd_ = hwnd;
        frames_captured_ = 0;
        acquire_timeouts_ = 0;
        access_lost_count_ = 0;
        last_frame_timestamp_us_ = 0;
        sequence_ = 0;
        last_copy_ = Clock::time_point{};
        fps_window_start_ = Clock::now();
        fps_window_frames_ = 0;
        {
            std::scoped_lock lock(frame_mutex_);
            latest_.reset();
        }
        {
            std::scoped_lock lock(state_mutex_);
            capture_fps_ = 0.0;
            capture_width_ = 0;
            capture_height_ = 0;
            adapter_name_.clear();
            last_error_.clear();
            capture_mode_.clear();
            notice_.clear();
        }
    }

    void start_worker(const bool window) {
        running_ = true;
        worker_ = std::thread([this, window] {
            const HRESULT com_result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
            const bool uninitialize_com = SUCCEEDED(com_result);
            if (window) {
                run_window();
            } else {
                run();
            }
            if (uninitialize_com) {
                CoUninitialize();
            }
        });
    }

    void set_error(std::string error) {
        std::scoped_lock lock(state_mutex_);
        last_error_ = std::move(error);
    }

    void set_mode(const char* mode) {
        std::scoped_lock lock(state_mutex_);
        capture_mode_ = mode;
    }

    void set_notice(std::string notice) {
        std::scoped_lock lock(state_mutex_);
        notice_ = std::move(notice);
    }

    // Slightly under one frame, so a frame that is due is never held back by a whole interval.
    [[nodiscard]] Clock::duration min_frame_gap() const {
        return std::chrono::duration_cast<Clock::duration>(
            std::chrono::microseconds(1'000'000 / config_.target_fps) * 9 / 10);
    }

    void update_fps(const Clock::time_point now) {
        const auto elapsed = now - fps_window_start_;
        if (elapsed >= std::chrono::seconds(1)) {
            const double seconds = std::chrono::duration<double>(elapsed).count();
            std::scoped_lock lock(state_mutex_);
            capture_fps_ = static_cast<double>(fps_window_frames_) / seconds;
            fps_window_frames_ = 0;
            fps_window_start_ = now;
        }
    }

    // Publishes a copied frame. The pixel buffer of the previous frame is recycled only when no
    // Python reader still holds it.
    void publish(const std::shared_ptr<CapturedFrame>& frame) {
        frame->sequence = ++sequence_;
        last_frame_timestamp_us_ = frame->timestamp_us;
        ++frames_captured_;
        ++fps_window_frames_;
        std::scoped_lock lock(frame_mutex_);
        if (latest_ && latest_.use_count() == 1) {
            recycled_pixels_.swap(latest_->pixels);
        }
        latest_ = frame;
    }

    void run() {
        const HRESULT com_result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        const bool uninitialize_com = SUCCEEDED(com_result);

        while (running_) {
            DuplicationResources resources;
            std::string error;
            if (!resources.initialize(config_, error)) {
                set_error(std::move(error));
                for (int i = 0; i < 10 && running_; ++i) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(100));
                }
                continue;
            }

            {
                std::scoped_lock lock(state_mutex_);
                adapter_name_ = resources.adapter_name;
                capture_width_ = resources.resolved_region.width;
                capture_height_ = resources.resolved_region.height;
                last_error_.clear();
            }

            const UINT timeout_ms = std::max<UINT>(1, 1000U / config_.target_fps);
            bool recreate = false;

            while (running_ && !recreate) {
                // Pacing happens before the acquire, not after the copy. Waiting after the copy would
                // hold back a frame that arrives during the wait. Here we wait only until the minimum
                // gap since the last copy has passed, so the next acquire returns as soon as a frame is due.
                const auto since_copy = Clock::now() - last_copy_;
                if (since_copy < min_frame_gap()) {
                    std::this_thread::sleep_for(min_frame_gap() - since_copy);
                }

                // Reuse a pixel buffer from an earlier frame instead of allocating and zeroing one per frame.
                auto frame = std::make_shared<CapturedFrame>();
                frame->pixels.swap(recycled_pixels_);
                const auto result = resources.acquire(*frame, timeout_ms, error);
                switch (result) {
                case DuplicationResources::AcquireResult::frame:
                    // Pace from the arrival, when the copy starts. Pacing from the end of the copy would
                    // add the copy time to every interval and cost frames at high resolutions.
                    last_copy_ = Clock::time_point(std::chrono::microseconds(frame->arrival_us));
                    publish(frame);
                    break;
                case DuplicationResources::AcquireResult::timeout:
                    ++acquire_timeouts_;
                    break;
                case DuplicationResources::AcquireResult::access_lost:
                    ++access_lost_count_;
                    recreate = true;
                    break;
                case DuplicationResources::AcquireResult::failure:
                    set_error(error);
                    recreate = true;
                    break;
                }

                if (result != DuplicationResources::AcquireResult::frame) {
                    recycled_pixels_.swap(frame->pixels);  // not published: keep its buffer
                }
                update_fps(Clock::now());
            }

            if (running_ && recreate) {
                std::this_thread::sleep_for(std::chrono::milliseconds(120));
            }
        }

        if (uninitialize_com) {
            CoUninitialize();
        }
    }

    // Window capture. Windows Graphics Capture delivers only the chosen window, so windows on top of
    // it are not captured. Frames arrive on a pool thread; the worker keeps only the newest one and
    // copies it at the frame rate.
    void run_window() {
#if FPSCOACH_HAS_WGC
        std::string error;
        if (!start_window_session(error)) {
            stop_window_session();
            set_mode("region");
            set_notice("Window capture could not start (" + error +
                       "). Showing the screen area behind the window instead, so windows on top of it will appear.");
            run();
            return;
        }

        {
            std::unique_lock lock(gate_mutex_);
            while (running_) {
                update_fps(Clock::now());
                gate_cv_.wait_for(lock, std::chrono::milliseconds(50),
                                  [this] { return !running_ || static_cast<bool>(pending_); });
                if (!running_) {
                    break;
                }
                if (!pending_) {
                    continue;
                }
                // Honour the frame rate: a frame that arrives early waits only until the minimum gap
                // since the last copy has passed. Newer frames replace the pending one while we wait.
                const auto due = last_copy_ + min_frame_gap();
                while (running_ && Clock::now() < due) {
                    gate_cv_.wait_until(lock, due);
                }
                if (!running_ || !pending_) {
                    continue;
                }
                auto frame = pending_;
                pending_ = nullptr;
                const std::uint64_t arrival = pending_arrival_us_;
                lock.unlock();

                auto out = std::make_shared<CapturedFrame>();
                out->pixels.swap(recycled_pixels_);
                std::string copy_error;
                const auto copy_start = Clock::now();
                const bool copied = read_window_frame(frame, *out, copy_error);
                frame.Close();
                if (copied) {
                    out->arrival_us = arrival;
                    publish(out);
                    last_copy_ = copy_start;  // the pacing slot this copy used
                    set_error(std::string{});
                } else {
                    recycled_pixels_.swap(out->pixels);
                    set_error(copy_error);
                }
                update_fps(Clock::now());
                lock.lock();
            }
            if (pending_) {
                pending_.Close();
                pending_ = nullptr;
            }
        }
        stop_window_session();
#else
        run();
#endif
    }

#if FPSCOACH_HAS_WGC
    bool create_window_device(std::string& error) {
        constexpr D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
        D3D_FEATURE_LEVEL selected{};
        HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
                                       D3D11_CREATE_DEVICE_BGRA_SUPPORT, levels,
                                       static_cast<UINT>(std::size(levels)), D3D11_SDK_VERSION,
                                       &window_device_, &selected, &window_context_);
        if (FAILED(hr)) {
            error = hr_message("D3D11CreateDevice", hr);
            return false;
        }
        ComPtr<IDXGIDevice> dxgi;
        hr = window_device_.As(&dxgi);
        if (FAILED(hr)) {
            error = hr_message("Query IDXGIDevice", hr);
            return false;
        }
        winrt::com_ptr<::IInspectable> inspectable;
        hr = CreateDirect3D11DeviceFromDXGIDevice(dxgi.Get(), inspectable.put());
        if (FAILED(hr)) {
            error = hr_message("CreateDirect3D11DeviceFromDXGIDevice", hr);
            return false;
        }
        window_device_rt_ = inspectable.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();
        return true;
    }

    bool start_window_session(std::string& error) {
        try {
            if (!winrt::Windows::Graphics::Capture::GraphicsCaptureSession::IsSupported()) {
                error = "this Windows version does not support it; Windows 10 version 1903 or later is needed";
                return false;
            }
            if (!IsWindow(reinterpret_cast<HWND>(hwnd_))) {
                error = "the selected window is no longer open";
                return false;
            }
            if (!window_device_ && !create_window_device(error)) {
                return false;
            }

            auto interop = winrt::get_activation_factory<winrt::Windows::Graphics::Capture::GraphicsCaptureItem,
                                                         IGraphicsCaptureItemInterop>();
            winrt::Windows::Graphics::Capture::GraphicsCaptureItem item{nullptr};
            winrt::check_hresult(interop->CreateForWindow(reinterpret_cast<HWND>(hwnd_),
                                                          winrt::guid_of<ABI::Windows::Graphics::Capture::IGraphicsCaptureItem>(),
                                                          winrt::put_abi(item)));
            // A minimised window reports zero size; the pool needs at least 1x1.
            const auto size = item.Size();
            const int width = std::max(1, size.Width);
            const int height = std::max(1, size.Height);
            window_pool_width_ = width;
            window_pool_height_ = height;
            window_pool_ = winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::CreateFreeThreaded(
                window_device_rt_, winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized, 2,
                winrt::Windows::Graphics::SizeInt32{width, height});
            window_session_ = window_pool_.CreateCaptureSession(item);
            try {
                // Windows 10 2004 and later can hide the yellow capture border. Older builds cannot.
                window_session_.IsBorderRequired(false);
            } catch (const winrt::hresult_error&) {
            }
            window_session_.IsCursorCaptureEnabled(false);
            window_frame_token_ = window_pool_.FrameArrived(
                [this](winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool const& sender,
                       winrt::Windows::Foundation::IInspectable const&) { on_window_frame(sender); });
            {
                std::scoped_lock lock(state_mutex_);
                capture_width_ = width;
                capture_height_ = height;
                adapter_name_ = "window capture";
            }
            window_session_.StartCapture();
            return true;
        } catch (const winrt::hresult_error& exception) {
            error = winrt::to_string(exception.message());
            return false;
        }
    }

    void on_window_frame(const winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool& sender) {
        // Runs on the pool's thread.
        const auto frame = sender.TryGetNextFrame();
        if (!frame) {
            return;
        }
        const std::uint64_t arrival = now_us();
        const auto content = frame.ContentSize();
        if (content.Width != window_pool_width_.load() || content.Height != window_pool_height_.load()) {
            // The window was resized: rebuild the pool so frames match the new size.
            sender.Recreate(window_device_rt_, winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
                            2, content);
            window_pool_width_ = content.Width;
            window_pool_height_ = content.Height;
        }
        std::scoped_lock lock(gate_mutex_);
        if (pending_) {
            pending_.Close();  // superseded before the worker took it
        }
        pending_ = frame;
        pending_arrival_us_ = arrival;
        gate_cv_.notify_one();
    }

    bool ensure_window_staging(const D3D11_TEXTURE2D_DESC& source, std::string& error) {
        if (window_staging_ && window_staging_desc_.Width == source.Width &&
            window_staging_desc_.Height == source.Height && window_staging_desc_.Format == source.Format) {
            return true;
        }
        window_staging_.Reset();
        D3D11_TEXTURE2D_DESC desc = source;
        desc.MipLevels = 1;
        desc.ArraySize = 1;
        desc.SampleDesc.Count = 1;
        desc.SampleDesc.Quality = 0;
        desc.Usage = D3D11_USAGE_STAGING;
        desc.BindFlags = 0;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        desc.MiscFlags = 0;
        const HRESULT hr = window_device_->CreateTexture2D(&desc, nullptr, &window_staging_);
        if (FAILED(hr)) {
            error = hr_message("Create window staging texture", hr);
            return false;
        }
        window_staging_desc_ = desc;
        return true;
    }

    bool read_window_frame(const winrt::Windows::Graphics::Capture::Direct3D11CaptureFrame& frame,
                           CapturedFrame& out, std::string& error) {
        try {
            auto dxgi_access = frame.Surface().as<Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
            winrt::com_ptr<ID3D11Texture2D> texture;
            winrt::check_hresult(dxgi_access->GetInterface(winrt::guid_of<ID3D11Texture2D>(), texture.put_void()));

            D3D11_TEXTURE2D_DESC source_desc{};
            texture->GetDesc(&source_desc);
            if (!ensure_window_staging(source_desc, error)) {
                return false;
            }
            window_context_->CopyResource(window_staging_.Get(), texture.get());

            D3D11_MAPPED_SUBRESOURCE mapped{};
            const HRESULT hr = window_context_->Map(window_staging_.Get(), 0, D3D11_MAP_READ, 0, &mapped);
            if (FAILED(hr)) {
                error = hr_message("Map window staging texture", hr);
                return false;
            }
            struct TextureUnmapper {
                ID3D11DeviceContext* context;
                ID3D11Texture2D* texture;
                ~TextureUnmapper() { context->Unmap(texture, 0); }
            } unmap{window_context_.Get(), window_staging_.Get()};

            out.timestamp_us = now_us();
            out.width = static_cast<int>(source_desc.Width);
            out.height = static_cast<int>(source_desc.Height);
            out.stride = out.width * 4;
            out.pointer_visible = false;
            out.pointer_x = 0;
            out.pointer_y = 0;
            out.pixels.resize(static_cast<std::size_t>(out.stride) * static_cast<std::size_t>(out.height));
            const auto* source_bytes = static_cast<const std::uint8_t*>(mapped.pData);
            if (mapped.RowPitch == static_cast<UINT>(out.stride)) {
                std::memcpy(out.pixels.data(), source_bytes, out.pixels.size());
            } else {
                for (int row = 0; row < out.height; ++row) {
                    std::memcpy(out.pixels.data() + static_cast<std::size_t>(row * out.stride),
                                source_bytes + static_cast<std::size_t>(row) * mapped.RowPitch,
                                static_cast<std::size_t>(out.stride));
                }
            }
            return true;
        } catch (const winrt::hresult_error& exception) {
            error = "Window frame could not be read: " + winrt::to_string(exception.message());
            return false;
        }
    }

    void stop_window_session() {
        try {
            if (window_session_) {
                window_session_.Close();
            }
            if (window_pool_) {
                window_pool_.FrameArrived(window_frame_token_);
                window_pool_.Close();
            }
        } catch (const winrt::hresult_error&) {
        }
        window_session_ = nullptr;
        window_pool_ = nullptr;
        window_device_rt_ = nullptr;
        window_staging_.Reset();
        window_context_.Reset();
        window_device_.Reset();
    }
#endif
};

DesktopDuplicationCapture::DesktopDuplicationCapture() : impl_(std::make_unique<Impl>()) {}
DesktopDuplicationCapture::~DesktopDuplicationCapture() = default;

void DesktopDuplicationCapture::start(const CaptureConfig& config) { impl_->start(config); }
void DesktopDuplicationCapture::start_window(std::uint64_t hwnd, const CaptureConfig& fallback) {
    impl_->start_window(hwnd, fallback);
}
void DesktopDuplicationCapture::stop() { impl_->stop(); }
bool DesktopDuplicationCapture::running() const { return impl_->running(); }
std::shared_ptr<const CapturedFrame> DesktopDuplicationCapture::latest_frame() const {
    return impl_->latest_frame();
}
std::uint64_t DesktopDuplicationCapture::latest_sequence() const { return impl_->latest_sequence(); }
CaptureStats DesktopDuplicationCapture::stats() const { return impl_->stats(); }

std::vector<OutputInfo> DesktopDuplicationCapture::enumerate_outputs() {
    std::vector<OutputInfo> result;
    ComPtr<IDXGIFactory1> factory;
    if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) {
        return result;
    }

    for (UINT adapter_index = 0;; ++adapter_index) {
        ComPtr<IDXGIAdapter1> adapter;
        if (factory->EnumAdapters1(adapter_index, &adapter) == DXGI_ERROR_NOT_FOUND) {
            break;
        }
        DXGI_ADAPTER_DESC1 adapter_desc{};
        adapter->GetDesc1(&adapter_desc);

        for (UINT output_index = 0;; ++output_index) {
            ComPtr<IDXGIOutput> output;
            if (adapter->EnumOutputs(output_index, &output) == DXGI_ERROR_NOT_FOUND) {
                break;
            }
            DXGI_OUTPUT_DESC output_desc{};
            output->GetDesc(&output_desc);
            OutputInfo info{};
            info.adapter_index = adapter_index;
            info.output_index = output_index;
            info.adapter_name = utf8(adapter_desc.Description);
            info.output_name = utf8(output_desc.DeviceName);
            info.left = output_desc.DesktopCoordinates.left;
            info.top = output_desc.DesktopCoordinates.top;
            info.width = output_desc.DesktopCoordinates.right - output_desc.DesktopCoordinates.left;
            info.height = output_desc.DesktopCoordinates.bottom - output_desc.DesktopCoordinates.top;
            info.attached_to_desktop = output_desc.AttachedToDesktop != 0;
            result.push_back(std::move(info));
        }
    }
    return result;
}

} // namespace fpscoach
