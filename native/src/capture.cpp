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

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <iterator>
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
        for (int row = 0; row < frame.height; ++row) {
            std::memcpy(frame.pixels.data() + static_cast<std::size_t>(row * frame.stride),
                        source_bytes + static_cast<std::size_t>(row) * mapped.RowPitch,
                        static_cast<std::size_t>(frame.stride));
        }
        return AcquireResult::frame;
    }
};

} // namespace

class DesktopDuplicationCapture::Impl {
public:
    ~Impl() { stop(); }

    void start(const CaptureConfig& config) {
        stop();
        config_ = config;
        config_.target_fps = std::clamp(config_.target_fps, 1U, 240U);
        frames_captured_ = 0;
        acquire_timeouts_ = 0;
        access_lost_count_ = 0;
        last_frame_timestamp_us_ = 0;
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
        }
        running_ = true;
        worker_ = std::thread([this] { run(); });
    }

    void stop() {
        running_ = false;
        if (worker_.joinable()) {
            worker_.join();
        }
    }

    [[nodiscard]] bool running() const { return running_; }

    [[nodiscard]] std::optional<CapturedFrame> latest_frame() const {
        std::scoped_lock lock(frame_mutex_);
        return latest_;
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
        }
        return value;
    }

private:
    void set_error(std::string error) {
        std::scoped_lock lock(state_mutex_);
        last_error_ = std::move(error);
    }

    void run() {
        const HRESULT com_result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        const bool uninitialize_com = SUCCEEDED(com_result);
        std::uint64_t sequence = 0;

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

            const auto frame_interval = std::chrono::microseconds(1'000'000 / config_.target_fps);
            const UINT timeout_ms = std::max<UINT>(1, 1000U / config_.target_fps);
            auto fps_window_start = Clock::now();
            std::uint64_t fps_window_frames = 0;
            bool recreate = false;

            while (running_ && !recreate) {
                const auto iteration_start = Clock::now();
                CapturedFrame frame;
                const auto result = resources.acquire(frame, timeout_ms, error);
                switch (result) {
                case DuplicationResources::AcquireResult::frame: {
                    frame.sequence = ++sequence;
                    last_frame_timestamp_us_ = frame.timestamp_us;
                    ++frames_captured_;
                    ++fps_window_frames;
                    {
                        std::scoped_lock lock(frame_mutex_);
                        latest_ = std::move(frame);
                    }
                    break;
                }
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

                const auto now = Clock::now();
                const auto elapsed = now - fps_window_start;
                if (elapsed >= std::chrono::seconds(1)) {
                    const double seconds = std::chrono::duration<double>(elapsed).count();
                    std::scoped_lock lock(state_mutex_);
                    capture_fps_ = static_cast<double>(fps_window_frames) / seconds;
                    fps_window_frames = 0;
                    fps_window_start = now;
                }

                const auto spent = Clock::now() - iteration_start;
                if (spent < frame_interval) {
                    std::this_thread::sleep_for(frame_interval - spent);
                }
            }

            if (running_ && recreate) {
                std::this_thread::sleep_for(std::chrono::milliseconds(120));
            }
        }

        if (uninitialize_com) {
            CoUninitialize();
        }
    }

    CaptureConfig config_{};
    std::atomic<bool> running_{false};
    std::thread worker_;

    mutable std::mutex frame_mutex_;
    std::optional<CapturedFrame> latest_;

    mutable std::mutex state_mutex_;
    double capture_fps_{};
    int capture_width_{};
    int capture_height_{};
    std::string adapter_name_;
    std::string last_error_;

    std::atomic<std::uint64_t> frames_captured_{0};
    std::atomic<std::uint64_t> acquire_timeouts_{0};
    std::atomic<std::uint64_t> access_lost_count_{0};
    std::atomic<std::uint64_t> last_frame_timestamp_us_{0};
};

DesktopDuplicationCapture::DesktopDuplicationCapture() : impl_(std::make_unique<Impl>()) {}
DesktopDuplicationCapture::~DesktopDuplicationCapture() = default;

void DesktopDuplicationCapture::start(const CaptureConfig& config) { impl_->start(config); }
void DesktopDuplicationCapture::stop() { impl_->stop(); }
bool DesktopDuplicationCapture::running() const { return impl_->running(); }
std::optional<CapturedFrame> DesktopDuplicationCapture::latest_frame() const {
    return impl_->latest_frame();
}
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
