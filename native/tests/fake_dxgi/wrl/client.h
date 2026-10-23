// Test-only ComPtr: a single raw pointer, no reference counting (the fake objects are leaked).
#pragma once
namespace Microsoft {
namespace WRL {
template <typename T>
class ComPtr {
public:
    ComPtr() = default;
    T* Get() const { return ptr_; }
    T** GetAddressOf() { return &ptr_; }
    T* operator->() const { return ptr_; }
    explicit operator bool() const { return ptr_ != nullptr; }
    void Reset() { ptr_ = nullptr; }
    T* ptr_ = nullptr;
    template <typename U>
    HRESULT As(ComPtr<U>* out) const {
        out->ptr_ = dynamic_cast<U*>(ptr_);
        return out->ptr_ ? 0 : -1;
    }
};
}  // namespace WRL
}  // namespace Microsoft
