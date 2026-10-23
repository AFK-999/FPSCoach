// Test-only DXGI declarations. Method bodies are in fake_win.cpp.
#pragma once
#include "Windows.h"
#include "wrl/client.h"

using Microsoft::WRL::ComPtr;

#define DXGI_ERROR_WAIT_TIMEOUT ((HRESULT)0x887A0027L)
#define DXGI_ERROR_ACCESS_LOST ((HRESULT)0x887A0026L)
#define DXGI_ERROR_DEVICE_REMOVED ((HRESULT)0x887A0005L)
#define DXGI_ERROR_DEVICE_RESET ((HRESULT)0x887A0007L)
#define DXGI_ERROR_NOT_CURRENTLY_AVAILABLE ((HRESULT)0x887A0022L)
#define DXGI_ERROR_NOT_FOUND ((HRESULT)0x887A0002L)

struct DXGI_ADAPTER_DESC1 { WCHAR Description[128]; };
struct DXGI_OUTPUT_DESC { WCHAR DeviceName[32]; RECT DesktopCoordinates; BOOL AttachedToDesktop; };
struct DXGI_OUTDUPL_POINTER_POSITION { POINT Position; BOOL Visible; };
struct DXGI_OUTDUPL_FRAME_INFO { std::uint64_t LastPresentTime; DXGI_OUTDUPL_POINTER_POSITION PointerPosition; };

struct IDXGIResource {
    virtual ~IDXGIResource() = default;
};
struct IDXGIOutputDuplication;
struct ID3D11Device;
struct IDXGIOutput {
    virtual ~IDXGIOutput() = default;
    void GetDesc(DXGI_OUTPUT_DESC* desc);
    int index = 0;
};
struct IDXGIOutput1 : IDXGIOutput {
    HRESULT DuplicateOutput(ID3D11Device* device, ComPtr<IDXGIOutputDuplication>* out);
};
struct IDXGIAdapter1 {
    virtual ~IDXGIAdapter1() = default;
    HRESULT EnumOutputs(UINT index, ComPtr<IDXGIOutput>* out);
    void GetDesc1(DXGI_ADAPTER_DESC1* desc);
};
struct IDXGIFactory1 {
    HRESULT EnumAdapters1(UINT index, ComPtr<IDXGIAdapter1>* out);
};
struct IDXGIOutputDuplication {
    HRESULT AcquireNextFrame(UINT timeout_ms, DXGI_OUTDUPL_FRAME_INFO* info, ComPtr<IDXGIResource>* resource);
    HRESULT ReleaseFrame();
};
HRESULT CreateDXGIFactory1(const void*, ComPtr<IDXGIFactory1>* out);
