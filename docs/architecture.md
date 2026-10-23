# Architecture

## Process boundary

The first release uses one desktop process with a strict ownership split:

- **C++ (`fpscoach_native`)** owns DXGI/D3D11 objects, the capture thread, frame copies, movement feature extraction, and curve generation.
- **Python/PySide6 (`fps_coach`)** owns navigation, beginner guidance, controls, preview presentation, calibration orchestration, curve-file adapters, optional chat-provider calls, and the scratch recorder.
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

## Scratch recording (Capture tab)

`screen_recorder.py` is a Qt-free engine: a capture thread grabs the primary display with `mss`, writes BGRA→BGR frames into a `cv2.VideoWriter`, and publishes each frame into a single-slot, sequence-numbered `LatestFrameBuffer` (readers always get a private copy; old frames are overwritten so a slow consumer adds no latency). A second thread fires an `analysis_callback` every `analysis_interval` seconds on an absolute schedule, skips ticks that arrive before the first frame, does not replay missed ticks, and survives callback exceptions.

`capture_recording.py` owns one instance as `RecordingService` and republishes its state as Qt signals (`started`, `analysis`, `stopped`, `failed`), so the page never touches a worker thread. The Capture tab's Recording card starts and stops it; its 250 ms stats timer polls the raw counters. `stop()`/`shutdown()` join both threads, release the encoder, close the `mss` handle on the capture thread that created it, and unlink the temporary file; `MainWindow.closeEvent` calls `shutdown()` so closing the app never leaves a recording behind. If `mss`, `numpy` or `opencv-python-headless` is missing (the `recorder` extra), the card disables itself and says what to install.

The recording is a scratch file for the user's own review: it is deleted when recording stops, is never uploaded, and is not analysed for targets, aim, or gameplay events. The analysis tick only samples frame statistics (size and mean brightness) as a pipeline health check. `tools/screen_recorder.py` is a command-line front end for the same engine.

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

## Curve compatibility

`curve_io.py` translates between a validated neutral profile and two user-selected formats. Legacy `.ccurve` files use six whitespace-delimited rows (horizontal speeds, multipliers, corner markers, then vertical equivalents). `.cc4` files use JSON and pipe-delimited points. Unknown CC4 fields are retained when an imported profile is re-exported. Generated normalized response points are converted to gain points over a 0–100 input-speed range. Import/export never applies a profile.

## Optional AI chat

`ai_chat.py` uses standard-library HTTP on a background `QThread`. Local mode calls Ollama's `/api/chat` endpoint and defaults to `qwen3:1.7b`; `phi4-mini` is another suggested tag. Online mode calls a user-supplied OpenAI-compatible `/v1/chat/completions` endpoint. Provider URL, model, and temperature can be stored in `QSettings`; API keys remain in memory only.

Only conversation text and a compact numeric calibration summary are included in model requests. Frames and raw movement samples are not included. The system prompt keeps the assistant inside coaching, comfort, and manual curve interpretation boundaries.

## Scope and security boundary

Included:

- documented Desktop Duplication capture,
- an explicit in-app target calibration,
- local feature extraction and recommendations,
- explicit `.ccurve` / `.cc4` file import and export,
- optional user-configured local or online chat,
- a low-rate preview.

Intentionally excluded:

- game-process access, memory reading, or DLL injection,
- target/enemy detection,
- input hooks, synthetic mouse input, recoil compensation, or aim automation,
- background or unattended recording, telemetry, automatic uploads, or automatic model downloads (the scratch recording is user-started, stops with the app, and is deleted on stop),
- automatic modification of game or driver settings.

The application should remain useful when capture is disabled. Game-specific policy always takes precedence; users should close capture where external capture software is prohibited.
