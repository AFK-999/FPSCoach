# FPS Coach — Windows desktop foundation

A lightweight, local-first foundation for an FPS movement/sensitivity coach. It combines a PySide6 beginner interface with a native C++ core for Desktop Duplication capture and explainable cursor-movement analysis.

> **Fair-play boundary:** this project is a coaching/calibration tool, not an aiming tool. It does not inspect game memory, identify targets, inject code, hook or synthesize input, compensate recoil, or change settings automatically.

## What is included

- **Beginner guide UI** with a measure → adjust → verify workflow.
- **Desktop Duplication capture** (`IDXGIOutput1::DuplicateOutput`) on a dedicated C++ worker.
- **Python capture fallback** — without the native core, the Capture tab still streams a live preview through `mss` (up to 60 FPS), so a `FPSCOACH_SKIP_NATIVE` install or a developer machine is not left with an empty page.
- **Integrated-GPU support** by creating D3D11 on the selected output's owning adapter and defaulting to a bounded 640×360 GPU-side crop.
- **Latest-frame buffering** so a slow preview drops stale work instead of accumulating latency.
- **Recording on the Capture tab** — records the primary display in the background and samples the newest frame on a configurable analysis timer. Discard the file when you stop (default) or keep it in a folder you choose.
- **Display-change recovery** for `DXGI_ERROR_ACCESS_LOST`.
- **In-app 12-target calibration** with ~125 Hz sampling during an explicit session.
- **Native movement model** that measures path efficiency, overshoot, underreach, corrections, oscillation, endpoint error, speed, and duration.
- **Conservative custom curve recommendation** with monotonic points, sensitivity multiplier, confidence, and plain-language reasons.
- **Custom Curve compatibility** for importing/exporting modern `.cc4` JSON profiles and legacy `.ccurve` text profiles, selected explicitly in the UI.
- **In-Game Mode (Settings → Advanced)** — off at every launch. Turn it on for a session, pick your game, and enter your DPI and in-game sensitivity; Vantage converts that to cm/360°, shows what its recommendation means in that game's units, and can ask the coach for a specific value. It never reads or changes the game.
- **Live vision (AI Coach)** — off at every launch. Turn it on and the coach watches the live capture preview: it samples the preview into short clips (4 frames half a second apart by default), measures what moved between them, and comments on the movement and the setup. **Look now** takes a clip on demand. It is instructed never to analyse targets, enemies, or aim. Needs a vision model (for example `gemma3:4b`); the card says so before you start.
- **Optional AI coach** using local Ollama models (defaults to `qwen3:1.7b`, with `phi4-mini` as an option) or a user-configured OpenAI-compatible online API. Settings → AI coach lists the models your provider actually offers, so a model can be re-picked from a dropdown instead of retyped.
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
│   ├── curve_io.py             # .ccurve and .cc4 validated adapters
│   ├── ai_chat.py              # Ollama/OpenAI-compatible chat clients
│   ├── screen_recorder.py      # scratch recorder engine (capture + analysis timer)
│   ├── capture_recording.py    # Qt service that drives the recorder
│   └── ui/                     # PySide6 pages and calibration canvas
├── docs/architecture.md
├── scripts/bootstrap.ps1
├── tools/screen_recorder.py    # command-line front end for the recorder engine
└── tests/                      # Python reference-model tests
```

## Windows prerequisites

- Windows 10/11
- Python 3.10–3.12 (3.11 recommended; 3.13 builds the native core unreliably)
- Visual Studio 2022 Build Tools with **Desktop development with C++**
- A recent Windows SDK and CMake 3.23+
- A WDDM driver with DXGI 1.2 Desktop Duplication support

No CUDA or discrete GPU is required.

### If `pip install -e` fails at the native link step

A failure that looks like `bindings.obj : error LNK2001: unresolved external symbol
__imp__PyThread_tss_create` (many `__imp__Py...` symbols, ending in `LNK1120: 149
unresolved externals`) means the C++ extension could not link against your Python's
import library — usually a Python/toolchain mismatch, most often Python 3.13. The Python
side of the app does not need that extension. Pick one:

```powershell
# 1. Install the Python package without the native core (no compiler needed at all)
$env:FPSCOACH_SKIP_NATIVE = "1"
pip install -e ".[dev,recorder]"

# 2. Or just install the optional packages and run from source
pip install numpy mss opencv-python-headless
python -m fps_coach

# 3. Or use the supported interpreter for the native build
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,recorder]"
```

With the native core missing, `fps_coach.backend` uses its portable fallback analyzer, and
the Capture tab switches to `mss` for the live preview (when it is installed) or explains
what to install; recording and In-Game Mode are pure Python and work normally either way.

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
python -m pip install -e ".[dev,recorder]"
python -m fps_coach
```

`pip install -e .` invokes scikit-build-core/CMake and installs `fpscoach_native.pyd` beside the Python package.

The Capture tab works in three tiers, and each one says which is in use:

1. **Native C++ core** (default Windows build): Desktop Duplication on the GPU, up to 120 FPS, can capture a single window even when other windows cover it.
2. **`mss` fallback** (no native core, `mss` installed): live preview of any display up to 60 FPS on the CPU. A window is captured as the screen area behind it, and **GPU capture (D3D11)** is disabled because it does not apply.
3. **Neither**: the Capture card lists no outputs, Start stays disabled, and the card names the command to install.

`numpy`, `mss` and `opencv-python-headless` — the `recorder` extra — cover both the live fallback and the Recording card. Install them with `pip install -e ".[dev,recorder]"`; the app starts without them and explains what is missing.

## First-use flow

1. Open **Beginner guide** and retain a copy of your current mouse/game profile.
2. In **Capture**, select the output that shows your borderless-windowed game and press **Start** — the preview above updates live. Start with **Center 640×360** and 30 or 60 FPS on integrated graphics. The **Recording** card below it can record the primary display for your own review: untick **Keep the recording** to discard it at Stop, or leave it ticked to save it to the folder you choose.
3. In **Calibration**, click all 12 targets using your normal grip and sensitivity.
4. Review **Recommendation**. The `x` coordinate is normalized input speed and `y` is normalized output speed.
5. In **Recommendation**, select **Custom Curve 4 (.cc4)** or **Custom Curve 3 / legacy (.ccurve)**, then import or export explicitly. Export creates a profile file; it never applies one.
6. Optionally open **AI chat** to discuss the result with a local or online model.
7. Test over several sessions. Revert if comfort or consistency declines.

### Live vision

The AI Coach tab's **Live vision** panel streams the Capture preview to your configured model
as ordered frames. It is **hidden until you enable it** — Settings (the gear in the chat card) →
**Enable live vision** — so the tab is just the chat if you never use it. Enabling it only shows
the panel; its **Watch live** switch still starts off at every launch. It works in clips rather
than snapshots:

- **Clip length** (default 4 frames) and **Sample every** (default 0.5 s) control the clip: four
  frames half a second apart is about 1.5 s of a moving screen in each answer.
- **Ask every** (default 4 s) is the gap after each reply; **Only when the picture changes**
  skips requests while the preview is still, so a paused game costs nothing.
- The card shows the **filmstrip that was sent** and a **motion meter** ("low · 2% (bottom)"),
  so you can see what the model got and how much of the frame was moving.

A local model cannot accept a video stream, so "real time" here means continuous sampling — the
model compares frames and answers about change. Nothing is queued: collecting stops while a
request is in flight and begins again from whatever is on screen when the reply lands. A text-only model such as `qwen3:1.7b` cannot see images — the card
names a vision model to pull instead:

```
ollama pull qwen3-vl:8b
```

`qwen3-vl` is the recommended default: it is trained on interleaved "timestamp + video frames"
input, and llama.cpp (which Ollama runs) merges consecutive qwen-vl images into a video block, so
the clip reaches it as video rather than as unrelated pictures. `gemma3:4b` also works. Every
frame's offset is spelled out in the prompt (`Frame times, oldest first: 0.0 s, 0.5 s, 1.0 s`),
because Ollama gives the model images without temporal position ids.

With a local Ollama server the frames never leave the machine; with an online provider they
are uploaded to it. See `docs/PRIVACY.md`.

Screen capture and calibration are independent in this foundation: pixels are available for future, policy-compliant local context models, while the current curve model uses only the explicit calibration movements.

## AI chat setup

The chat interface is optional and uses the Python standard library—no AI SDK is required.

For local inference, install [Ollama](https://ollama.com), then pull one small model:

```powershell
ollama pull qwen3:1.7b
# or
ollama pull phi4-mini
```

Keep **Provider: Local Ollama**, use `http://127.0.0.1:11434`, and select the matching model tag. Prompts stay on the configured local server.

For an online or self-hosted OpenAI-compatible service, select **Online / OpenAI-compatible API**, enter the base URL, model, and API key. The key is held in memory and is not stored in `QSettings`. Online mode sends the conversation and current calibration summary to the configured provider; screen frames are never included.

## Curve file compatibility

- `.ccurve` support reads/writes the legacy six-row horizontal/vertical point representation.
- `.cc4` support reads/writes Custom Curve 4 JSON point strings and preserves unknown CC4 fields when re-exporting an imported profile.
- Normalized recommendations are converted to input-speed/gain points over a 0–100 speed range. Review the result in Custom Curve before applying it.
- Imported files are validated for finite, non-negative, speed-sorted points and bounded point counts.

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
- `.ccurve` and `.cc4` cover Custom Curve compatibility only; other vendor/game formats still require explicit, reviewed adapters.
- Chat quality and hardware requirements depend on the chosen model/provider. FPS Coach does not download model weights automatically.
- The current pixel path is capture infrastructure only; it does not perform target or enemy analysis.
