// Test-only implementation of the DXGI / D3D11 calls used by capture.cpp.
// A synthetic desktop updates every ~16.7 ms. Pixel (x, y) of desktop frame N is BGRA
// (x & 255, y & 255, N & 255, 255), so a consumer can check which frame it received.
#include "d3d11.h"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <thread>

namespace {
std::atomic<std::uint64_t> g_desktop_frame{0};
std::chrono::steady_clock::time_point g_next_due = std::chrono::steady_clock::now();
// Time (steady clock, microseconds) at which each desktop frame appeared, indexed by frame number
// modulo the table size. Lets a test measure desktop-update-to-screen latency.
constexpr std::size_t kFrameTimeSlots = 1u << 20;
std::uint64_t g_frame_times_us[kFrameTimeSlots];
constexpr int kDesktopWidth = 1920;
constexpr int kDesktopHeight = 1080;

int pitch_padding() {
    const char* value = std::getenv("FAKE_PITCH_PAD");
    return value ? std::atoi(value) : 0;
}
}  // namespace

int WideCharToMultiByte(unsigned, unsigned long, const wchar_t* in, int, char* out, int out_size, const char*,
                        const char*) {
    int n = 0;
    for (; in[n] != 0; ++n) {
        if (out && n + 1 < out_size) {
            out[n] = static_cast<char>(in[n]);
        }
    }
    if (out && n < out_size) {
        out[n] = 0;
    }
    return n + 1;
}
HRESULT CoInitializeEx(void*, unsigned long) { return 0; }
void CoUninitialize() {}
BOOL GetCursorPos(POINT* point) {
    point->x = 100;
    point->y = 100;
    return 1;
}

HRESULT CreateDXGIFactory1(const void*, ComPtr<IDXGIFactory1>* out) {
    out->ptr_ = new IDXGIFactory1();
    return 0;
}

HRESULT IDXGIFactory1::EnumAdapters1(UINT index, ComPtr<IDXGIAdapter1>* out) {
    if (index > 0) {
        return DXGI_ERROR_NOT_FOUND;
    }
    out->ptr_ = new IDXGIAdapter1();
    return 0;
}

HRESULT IDXGIAdapter1::EnumOutputs(UINT index, ComPtr<IDXGIOutput>* out) {
    if (index > 0) {
        return DXGI_ERROR_NOT_FOUND;
    }
    out->ptr_ = new IDXGIOutput1();
    return 0;
}

void IDXGIAdapter1::GetDesc1(DXGI_ADAPTER_DESC1* desc) {
    const wchar_t* name = L"FAKE GPU";
    std::wcsncpy(desc->Description, name, 127);
}

void IDXGIOutput::GetDesc(DXGI_OUTPUT_DESC* desc) {
    std::wcsncpy(desc->DeviceName, L"FAKE-1", 31);
    desc->DesktopCoordinates = RECT{0, 0, kDesktopWidth, kDesktopHeight};
    desc->AttachedToDesktop = 1;
}

HRESULT IDXGIOutput1::DuplicateOutput(ID3D11Device*, ComPtr<IDXGIOutputDuplication>* out) {
    out->ptr_ = new IDXGIOutputDuplication();
    return 0;
}

HRESULT IDXGIOutputDuplication::AcquireNextFrame(UINT timeout_ms, DXGI_OUTDUPL_FRAME_INFO* info,
                                                 ComPtr<IDXGIResource>* resource) {
    const auto now = std::chrono::steady_clock::now();
    if (g_next_due > now) {
        const auto wait = g_next_due - now;
        if (wait > std::chrono::milliseconds(timeout_ms)) {
            std::this_thread::sleep_for(std::chrono::milliseconds(timeout_ms));
            return DXGI_ERROR_WAIT_TIMEOUT;
        }
        std::this_thread::sleep_for(wait);
    }
    g_next_due += std::chrono::microseconds(16667);
    if (g_next_due < std::chrono::steady_clock::now()) {
        g_next_due = std::chrono::steady_clock::now();
    }
    const std::uint64_t frame = ++g_desktop_frame;
    g_frame_times_us[frame % kFrameTimeSlots] = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    info->LastPresentTime = frame;
    info->PointerPosition.Visible = 1;
    info->PointerPosition.Position = POINT{300, 200};
    auto* texture = new ID3D11Texture2D();
    texture->frame_number = frame;
    texture->desc.Width = kDesktopWidth;
    texture->desc.Height = kDesktopHeight;
    texture->desc.Format = 87;  // DXGI_FORMAT_B8G8R8A8_UNORM
    texture->desc.Usage = D3D11_USAGE_DEFAULT;
    resource->ptr_ = texture;
    return 0;
}

HRESULT IDXGIOutputDuplication::ReleaseFrame() { return 0; }

HRESULT ID3D11Device::CreateTexture2D(const D3D11_TEXTURE2D_DESC* desc, const void*,
                                      ComPtr<ID3D11Texture2D>* out) {
    auto* texture = new ID3D11Texture2D();
    texture->desc = *desc;
    texture->pitch = desc->Width * 4 + static_cast<UINT>(pitch_padding());
    texture->buffer.reset(new std::uint8_t[static_cast<std::size_t>(texture->pitch) * desc->Height]());
    out->ptr_ = texture;
    return 0;
}

void ID3D11Texture2D::GetDesc(D3D11_TEXTURE2D_DESC* out) { *out = desc; }


void ID3D11DeviceContext::CopySubresourceRegion(ID3D11Texture2D* dst, UINT, UINT, UINT, UINT,
                                                ID3D11Texture2D* src, UINT, const D3D11_BOX* box) {
    // Fill the staging buffer with the source frame's pattern, as a GPU copy would.
    const UINT width = box->right - box->left;
    const UINT height = box->bottom - box->top;
    const std::uint8_t frame_byte = static_cast<std::uint8_t>(src->frame_number & 255);
    for (UINT y = 0; y < height && y < dst->desc.Height; ++y) {
        std::uint8_t* row = dst->buffer.get() + static_cast<std::size_t>(y) * dst->pitch;
        for (UINT x = 0; x < width && x < dst->desc.Width; ++x) {
            std::uint8_t* px = row + static_cast<std::size_t>(x) * 4;
            px[0] = static_cast<std::uint8_t>((x + box->left) & 255);
            px[1] = static_cast<std::uint8_t>((y + box->top) & 255);
            px[2] = frame_byte;
            px[3] = 255;
        }
    }
    dst->frame_number = src->frame_number;
}

HRESULT ID3D11DeviceContext::Map(ID3D11Texture2D* texture, UINT, D3D11_MAP, UINT, D3D11_MAPPED_SUBRESOURCE* mapped) {
    mapped->pData = texture->buffer.get();
    mapped->RowPitch = texture->pitch;
    mapped->DepthPitch = texture->pitch * texture->desc.Height;
    return 0;
}

void ID3D11DeviceContext::Unmap(ID3D11Texture2D*, UINT) {}

HRESULT D3D11CreateDevice(IDXGIAdapter1*, D3D_DRIVER_TYPE, void*, UINT, const D3D_FEATURE_LEVEL*, UINT, UINT,
                          ComPtr<ID3D11Device>* device, D3D_FEATURE_LEVEL* selected,
                          ComPtr<ID3D11DeviceContext>* context) {
    device->ptr_ = new ID3D11Device();
    context->ptr_ = new ID3D11DeviceContext();
    *selected = D3D_FEATURE_LEVEL_11_0;
    return 0;
}

// Test hooks, loaded with ctypes by the benchmark. Not used by the product.
extern "C" std::uint64_t fake_desktop_frame_count() { return g_desktop_frame.load(); }
extern "C" std::uint64_t fake_desktop_frame_time_us(std::uint64_t frame) {
    return g_frame_times_us[frame % kFrameTimeSlots];
}
