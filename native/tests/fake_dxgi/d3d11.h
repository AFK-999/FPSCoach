// Test-only D3D11 declarations. Method bodies are in fake_win.cpp.
#pragma once
#include "dxgi1_2.h"

enum D3D_FEATURE_LEVEL { D3D_FEATURE_LEVEL_11_1 = 0xb100, D3D_FEATURE_LEVEL_11_0 = 0xb000, D3D_FEATURE_LEVEL_10_1 = 0xa100, D3D_FEATURE_LEVEL_10_0 = 0xa000 };
enum D3D_DRIVER_TYPE { D3D_DRIVER_TYPE_UNKNOWN = 0 };
#define D3D11_CREATE_DEVICE_BGRA_SUPPORT 0x20
#define D3D11_SDK_VERSION 7
enum D3D11_USAGE { D3D11_USAGE_DEFAULT = 0, D3D11_USAGE_STAGING = 3 };
enum D3D11_CPU_ACCESS_FLAG { D3D11_CPU_ACCESS_READ = 0x20000 };
enum D3D11_MAP { D3D11_MAP_READ = 1 };
struct DXGI_SAMPLE_DESC_STUB { UINT Count; UINT Quality; };
struct D3D11_TEXTURE2D_DESC {
    UINT Width; UINT Height; UINT MipLevels; UINT ArraySize; int Format;
    DXGI_SAMPLE_DESC_STUB SampleDesc; D3D11_USAGE Usage; UINT BindFlags; UINT CPUAccessFlags; UINT MiscFlags;
};
struct D3D11_BOX { UINT left; UINT top; UINT front; UINT right; UINT bottom; UINT back; };
struct D3D11_MAPPED_SUBRESOURCE { void* pData; UINT RowPitch; UINT DepthPitch; };

struct ID3D11Resource {
    virtual ~ID3D11Resource() = default;
};
struct ID3D11Texture2D : IDXGIResource {
    D3D11_TEXTURE2D_DESC desc{};
    std::uint64_t frame_number = 0;  // the desktop frame this texture holds
    std::unique_ptr<std::uint8_t[]> buffer;
    UINT pitch = 0;
    void GetDesc(D3D11_TEXTURE2D_DESC* out);
};
struct ID3D11DeviceContext {
    void CopySubresourceRegion(ID3D11Texture2D* dst, UINT, UINT, UINT, UINT, ID3D11Texture2D* src, UINT,
                               const D3D11_BOX* box);
    HRESULT Map(ID3D11Texture2D* texture, UINT, D3D11_MAP, UINT, D3D11_MAPPED_SUBRESOURCE* mapped);
    void Unmap(ID3D11Texture2D* texture, UINT);
};
struct ID3D11Device {
    HRESULT CreateTexture2D(const D3D11_TEXTURE2D_DESC* desc, const void*, ComPtr<ID3D11Texture2D>* out);
};
HRESULT D3D11CreateDevice(IDXGIAdapter1* adapter, D3D_DRIVER_TYPE, void*, UINT, const D3D_FEATURE_LEVEL* levels,
                          UINT level_count, UINT, ComPtr<ID3D11Device>* device, D3D_FEATURE_LEVEL* selected,
                          ComPtr<ID3D11DeviceContext>* context);
