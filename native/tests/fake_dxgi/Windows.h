// Test-only stand-in for the Windows SDK pieces used by native/src/capture.cpp.
// It produces a synthetic desktop so the real capture worker can run on Linux.
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <memory>
#include <wchar.h>

typedef long HRESULT;
typedef unsigned int UINT;
typedef unsigned long DWORD;
typedef long LONG;
typedef int BOOL;
typedef wchar_t WCHAR;
typedef const char* LPCSTR;
typedef const wchar_t* LPCWSTR;
typedef void* LPVOID;
typedef const void* LPCVOID;
typedef void* HANDLE;
typedef void* HWND;

#define S_OK ((HRESULT)0)
#define E_INVALIDARG ((HRESULT)0x80070057L)
#define E_ACCESSDENIED ((HRESULT)0x80070005L)
#define FAILED(hr) (((HRESULT)(hr)) < 0)
#define SUCCEEDED(hr) (((HRESULT)(hr)) >= 0)
#define CP_UTF8 65001u
#define IID_PPV_ARGS(ppType) nullptr, (ppType)
#define COINIT_MULTITHREADED 0x0

int WideCharToMultiByte(unsigned, unsigned long, const wchar_t*, int, char*, int, const char*, const char*);
HRESULT CoInitializeEx(void*, unsigned long);
void CoUninitialize();
BOOL GetCursorPos(struct POINT* point);

struct POINT { LONG x; LONG y; };
struct RECT { LONG left; LONG top; LONG right; LONG bottom; };
