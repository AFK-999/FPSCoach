# FPS Coach — Windows desktop foundation

A lightweight, local-first foundation for an FPS movement/sensitivity coach. It combines a PySide6 beginner interface with a native C++ core for Desktop Duplication capture and explainable cursor-movement analysis.

> **Fair-play boundary:** this project is a coaching/calibration tool, not an aiming tool. It does not inspect game memory, identify targets, inject code, hook or synthesize input, compensate recoil, or change settings automatically.

## What is included

- **Beginner guide UI** with a measure → adjust → verify workflow.
- **Desktop Duplication capture** (`IDXGIOutput1::DuplicateOutput`) on a dedicated C++ worker.
- **Integrated-GPU support** by creating D3D11 on the selected output's owning adapter and defaulting to a bounded 640×360 GPU-side crop.
- **Latest-frame buffering** so a slow preview drops stale work instead of accumulating latency.
- **Display-change recovery** for `DXGI_ERROR_ACCESS_LOST`.
- **In-app 12-target calibration** with ~125 Hz sampling during an explicit session.
- **Native movement model** that measures path efficiency, overshoot, underreach, corrections, oscillation, endpoint error, speed, and duration.
- **Conservative custom curve recommendation** with monotonic points, sensitivity multiplier, confidence, and plain-language reasons.
- **Portable Python reference analyzer** so UI/model work can be previewed without the Windows extension.

## Layout

```text
.
├── CMakeLists.txt
├── native/
│   ├── include/fpscoach/       # capture and analyzer public APIs
│   ├── src/                    # D3D11/DXGI, model, pybind11 module
│   └── tests/                  # dependency-free C++ model test
├── python/fps_coach/
│   ├── backend.py              # narrow native facade + preview fallback
│   └── ui/                     # PySide6 pages and calibration canvas
├── docs/architecture.md
├── scripts/bootstrap.ps1
└── tests/                      # Python reference-model tests
```

## Windows prerequisites

- Windows 10/11
- Python 3.10–3.12 (3.11 recommended)
- Visual Studio 2022 Build Tools with **Desktop development with C++**
- A recent Windows SDK and CMake 3.23+
- A WDDM driver with DXGI 1.2 Desktop Duplication support

No CUDA or discrete GPU is required.

## Build and run

From a PowerShell prompt with the MSVC build tools available:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
.\scripts\run.ps1
```

Equivalent manual setup:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m fps_coach
```

`pip install -e .` invokes scikit-build-core/CMake and installs `fpscoach_native.pyd` beside the Python package.

## First-use flow

1. Open **Beginner guide** and retain a copy of your current mouse/game profile.
2. In **Capture**, select the output that shows your borderless-windowed game. Start with **Center 640×360** and 30 or 60 FPS on integrated graphics.
3. In **Calibration**, click all 12 targets using your normal grip and sensitivity.
4. Review **Recommendation**. The `x` coordinate is normalized input speed and `y` is normalized output speed.
5. If your approved mouse/game settings support a curve, create a **new** profile and enter/interpolate those points. Treat the sensitivity multiplier as a starting suggestion.
6. Test over several sessions. Revert if comfort or consistency declines.

Screen capture and calibration are independent in this foundation: pixels are available for future, policy-compliant local context models, while the current curve model uses only the explicit calibration movements.

## Performance notes

- The capture thread retains one frame, avoiding latency-producing queues.
- Center-cropping occurs before staging readback. It is a crop, not a scale operation.
- Python requests preview pixels only four times per second; the native worker can continue at its configured rate.
- Full 1080p BGRA readback at 60 FPS is about 475 MiB/s before overhead and can contend heavily with an iGPU's shared memory. Prefer the bounded region.
- On hybrid laptops, choose the adapter/output pair that owns the display. The code never assumes adapter 0 is discrete or faster.
- Desktop Duplication reports changed desktop frames. A static desktop can produce acquire timeouts and a measured FPS below the selected cap; that is expected.

See [docs/architecture.md](docs/architecture.md) for the ownership model, capture pipeline, feature math, and scope boundaries.

## Tests

Python reference model:

```powershell
python -m pytest
```

Native analyzer (no test framework dependency):

```powershell
cmake -S . -B build/native-tests -DBUILD_TESTING=ON
cmake --build build/native-tests --config Release
ctest --test-dir build/native-tests -C Release --output-on-failure
```

## Known foundation-level limitations

- Borderless-windowed mode is recommended. Some exclusive-fullscreen paths, secure desktop content, and protected surfaces cannot be captured.
- The preview assumes the common Desktop Duplication BGRA8 layout; an HDR/color-management stage is not yet included.
- Cursor calibration observes Qt/Windows desktop coordinates only while the in-app task is running. It does not attempt to read a game's raw-input stream.
- Curves are normalized recommendations because vendor/game curve formats differ. Add an explicit, user-reviewed exporter per supported format rather than silently writing settings.
- The current pixel path is capture infrastructure only; it does not perform target or enemy analysis.
