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

## Capture without the native core (rev78)

`fps_coach_native` is optional (`FPSCOACH_SKIP_NATIVE`, see `docs/build-and-packaging.md`). When it is absent, `fps_coach.python_capture.PythonDesktopCapture` stands in and `CoreBackend` picks it automatically:

| native method | Python equivalent |
| --- | --- |
| `enumerate_outputs()` | `sct.monitors[1:]`, one entry per physical monitor |
| `start(...)` | `plan_region()` + a grab thread |
| `stats()` | same twelve keys, including `capture_mode`, `notice`, `last_frame_age_ms` |
| `latest_frame()` / `latest_sequence()` | same frame dict, BGRA bytes and sequence number |

`plan_region()` reproduces the preset semantics the native path has: `left`/`top` of `-1` centers the region in the monitor (that is what **Balanced** and **Performance** send), any other value is a window origin and is clamped so the grab never leaves the monitor. The Python path is capped at 60 FPS and reports that in `stats()["notice"]` when 120 was requested; it cannot capture an occluded window, so **Window** selection captures the screen area behind it and says so. Enumerating displays returns `[]` with the reason in `CoreBackend.capture_error` when no display can be opened (RDP, locked session), instead of raising during page construction.

`tests/test_python_capture.py` covers all of this with an injected `mss` double; no test touches a display.

## Live vision (rev79)

`visual_coach.py` is Qt-free and does four things:

1. `frame_signature()` samples a 32×18 luma grid straight out of the BGRA buffer — one pixel per
   cell, no resize, no dependency. `frame_delta()` compares two signatures; below the threshold
   (0.4% mean luma) the frame counts as unchanged and is not re-sent, so a paused game does not
   produce identical comments.
2. `encode_frame()` produces a JPEG capped at 768 px wide: OpenCV when the recorder extra is
   installed, otherwise Qt's writer, so a plain install still works.
3. `LiveVisionSession` runs the loop as *windows*, not snapshots: sample the newest frame every
   `sample_interval` seconds until `frames_per_request` frames are collected, send them together,
   wait `min_interval`, repeat. `motion_report()` compares their signatures and reports the level
   and which third of the frame moved; that sentence goes into the prompt, and the same figure
   drives the motion meter. A still screen stops costing requests (`idle_motion` / `idle_wait`),
   repeated frames inside a window are dropped, `max_payload_bytes` trims the oldest frames of an
   oversized window, and provider errors back off exponentially until `max_consecutive_errors`.
   Nothing is queued: collection stops while a request is in flight, so a slow model means fewer,
   fresher windows instead of a backlog.
4. The prompt (`COACH_PROMPT` / `LOOK_PROMPT`) describes the clip ("4 ordered frames, sampled
   every 0.5 s, together about 1.5 s of a moving screen") plus the measured motion, and restricts
   the model to the picture and the setup: what changed and where, layout, readability, HUD
   clutter, lighting, colour, blur. It is told never to identify targets, enemies, or players and
   never to give aim, recoil, or positioning guidance. A local Ollama model cannot ingest a video
   stream, so ordered sampling is what "watching" means here: the loop samples ~2 FPS of coverage
   while the model keeps up.

`ai_chat.AIChatClient.chat(..., images=[jpeg])` carries the picture in the shape each provider
expects: base64 under `images` on the Ollama message, `image_url` parts for OpenAI-compatible
endpoints, with `VISION_SYSTEM_PROMPT` added (never replacing `SYSTEM_PROMPT`).
`model_supports_images()` is a name heuristic used only to warn in the UI before a request fails.

The Qt side is `AIChatPage`'s **Live vision** card plus `threads._LiveVisionThread`, which turns
the session's callbacks into signals so the page only touches widgets on the UI thread. The page
also shows the exact JPEG that was sent, so there is no doubt about what the model saw.

## Scratch recording (Capture tab)

`screen_recorder.py` is a Qt-free engine: a capture thread grabs the primary display with `mss`, writes BGRA→BGR frames into a `cv2.VideoWriter`, and publishes each frame into a single-slot, sequence-numbered `LatestFrameBuffer` (readers always get a private copy; old frames are overwritten so a slow consumer adds no latency). A second thread fires an `analysis_callback` every `analysis_interval` seconds on an absolute schedule, skips ticks that arrive before the first frame, does not replay missed ticks, and survives callback exceptions.

`capture_recording.py` owns one instance as `RecordingService` and republishes its state as Qt signals (`started`, `analysis`, `stopped`, `failed`), so the page never touches a worker thread. The Capture tab's Recording card starts and stops it; its 250 ms stats timer polls the raw counters. `stop()`/`shutdown()` join both threads, release the encoder, close the `mss` handle on the capture thread that created it, and then either unlink the file (default) or move it to `fpscoach-recording-<stamp>.mp4` in the folder chosen with **Keep the recording**; `MainWindow.closeEvent` calls `shutdown()`, so closing the app stops the recording and honours that choice. If `mss`, `numpy` or `opencv-python-headless` is missing (the `recorder` extra), the card disables itself and says what to install.

Recordings are user-controlled files for the user's own review. By default they are discarded at Stop; with **Keep** they stay in the chosen folder until deleted by the user. Nothing is uploaded, and the file is not analysed for targets, aim, or gameplay events: the analysis tick samples frame statistics (size and mean brightness) as a pipeline health check. `tools/screen_recorder.py` is a command-line front end for the same engine.

## In-Game Mode (Settings)

`ingame.py` is pure arithmetic over values the user types: a small catalog of games (`yaw` degrees per mouse count at in-game sensitivity 1.0), a cm/360° conversion with an optional measured 360° distance that overrides the catalog constant, and an `InGameMode` state holder. The Settings card (`SettingsPage._build_ingame_card`) exposes the session toggle, game, DPI, in-game sensitivity, measured distance, a live conversion line, and an **Ask the coach** button.

The mode is **off at every launch by construction**: `InGameMode.enabled` starts `False` and the toggle is never written to `QSettings`. Only `ingame/game`, `ingame/dpi`, `ingame/sensitivity` and `ingame/measured_cm360` persist.

When it is on, `MainWindow._sync_ingame_context()` appends `InGameMode.context_block()` to the coach's context; when it is off the block is empty and the model receives nothing. The block states the game, DPI, sensitivity, cm/360°, and what the app's current multiplier would be in-game, so the coach can answer with a concrete in-game number. `ai_chat.SYSTEM_PROMPT` describes that block and requires the model to present suggestions as the user's to apply, small and reversible, and never to claim it can read or change the game.

No part of this module touches a game: there is no window enumeration, input injection, process access, file access, or capture. `tests/test_ingame_mode.py` asserts that by walking the module's AST (stdlib `dataclasses` only, no `open`, `subprocess`, `ctypes`, or window/input APIs) as well as checking the behaviour.

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
- unattended recording that keeps running without the app open, telemetry, automatic uploads, or automatic model downloads (recording is started by the user and stops when the app closes; the file may be kept locally on request),
- automatic modification of game or driver settings.

The application should remain useful when capture is disabled. Game-specific policy always takes precedence; users should close capture where external capture software is prohibited.
