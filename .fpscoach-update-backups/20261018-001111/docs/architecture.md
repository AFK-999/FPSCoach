# Architecture

## Process boundary

The first release uses one desktop process with a strict ownership split:

- **C++ (`fpscoach_native`)** owns DXGI/D3D11 objects, the capture thread, frame copies, movement feature extraction, and curve generation.
- **Python/PySide6 (`fps_coach`)** owns navigation, beginner guidance, controls, preview presentation, and calibration orchestration.
- **pybind11** exposes coarse operations. Python polls only the latest frame and statistics; it does not participate in the native frame loop.

This keeps the foundation simple while allowing capture/analysis to move into a separate process later without changing the UI concepts.

## Capture path

1. Enumerate `IDXGIAdapter1` and `IDXGIOutput` pairs.
2. Create the D3D11 device on the **adapter that owns the chosen output**. This is important on hybrid laptops and supports integrated Intel/AMD graphics without assuming a discrete GPU.
3. Create `IDXGIOutputDuplication` and acquire frames on a C++ worker.
4. Crop on the GPU with `CopySubresourceRegion` into a reusable staging texture.
5. Map only that bounded region and copy row-pitched BGRA into a tightly packed latest-frame buffer.
6. Recover from display-mode changes and `DXGI_ERROR_ACCESS_LOST` by recreating all duplication resources.

The worker retains one latest frame rather than a queue. If Python paints slowly, stale previews are discarded instead of creating latency and memory pressure.

### Integrated-graphics defaults

The default 640×360 center region at 60 FPS is roughly 55 MiB/s of tight BGRA data before driver overhead. A 1920×1080 full-output readback at 60 FPS is roughly 475 MiB/s, which competes for shared memory bandwidth on an iGPU. Full output therefore remains opt-in.

Future pixel models should add a D3D11 compute shader (or DirectML execution provider) and keep tensors on the GPU. The current foundation intentionally avoids claiming a GPU downscale that it does not implement.

## Movement model

Calibration gives the native analyzer timestamped pointer positions plus each known target. Per trial it extracts:

- direct/path distance ratio (path efficiency),
- maximum forward projection beyond the target (overshoot),
- endpoint projection (underreach),
- backward segments and lateral sign changes (corrections),
- perpendicular RMS movement and reversals (small oscillation score),
- endpoint error, duration, and mean speed.

The recommendation model is deliberately small and explainable. Aggregate features adjust a bounded power curve, a low-speed damping term, and a conservative sensitivity multiplier. Curve points are forced to remain monotonic and to finish at `(1, 1)`. Confidence combines completed-trial count and trial consistency.

This is a starting recommendation, not a trained medical assessment or an automatic control system.

## Scope and security boundary

Included:

- documented Desktop Duplication capture,
- an explicit in-app target calibration,
- local feature extraction and recommendations,
- a low-rate preview.

Intentionally excluded:

- game-process access, memory reading, or DLL injection,
- target/enemy detection,
- input hooks, synthetic mouse input, recoil compensation, or aim automation,
- background recording, networking, telemetry, or cloud inference,
- automatic modification of game or driver settings.

The application should remain useful when capture is disabled. Game-specific policy always takes precedence; users should close capture where external capture software is prohibited.
