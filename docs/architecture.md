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
4. Frames carry their offsets in the prompt (`Frame times, oldest first: 0.0 s, 0.5 s, …`) because
   Ollama delivers them as images with no temporal position ids (ollama#18151). The recommended
   models are the qwen-vl family, which is trained on exactly that interleaved timestamp/frame
   format; llama.cpp's frame-merge support also combines consecutive qwen-vl images into a video
   block.
5. The prompt (`COACH_PROMPT` / `LOOK_PROMPT`) describes the clip ("4 ordered frames, sampled
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

## Calibration sampling and graph editing (rev89)

The explicit 12-target calibration offers requested cursor-sampling targets of 125, 250, 500, and
1,000 Hz, persisted at `calibration/sample_hz`; new installs default to 1,000 Hz. A precise Qt timer
uses a 1 ms interval at 1,000 Hz. This is a target cadence, not a guarantee: Windows/Qt scheduling
and UI load can lower the achieved rate. Mouse-move events are also recorded, and the canvas redraws
are throttled separately to about 60 Hz so collection does not force 1,000 paints per second. The
native and Python fallback analyzers each cap a trial at 65,536 movement samples (over 65 seconds at
1 kHz), preserving a 12-second high-polling-rate trial without unbounded memory use.

The **Calibration → Sensitivity curve** is editable. Each visible dot can be dragged: short curves
(64 points or fewer) draw a dot on every point; longer curves show up to 64 key bends, evenly sampling
them if a very wiggly curve has more. Drag vertically to change gain; drag an interior dot left or
right to change its normalized speed. The first and last dots stay at their original speed bounds.
Values between neighbouring bends follow the move smoothly, and a zero-gain dot uses an additive
adjustment so it is not stuck at zero. Hovering a dot shows the move cursor.

Right-click the plot for a theme-aware menu:

* **Add point here** inserts a point at the pointer's speed and gain. It is disabled outside the plot
  or once the chart reaches 200 points.
* **Delete point** removes the dot under the pointer. It is disabled unless the pointer is on a dot,
  and the chart always keeps at least two points.

Both actions change the profile that **Export** writes, not just the pixels on the chart: a new or
removed control point is mirrored into the matching horizontal and vertical curve points. Moving a
dot updates its speed and/or gain in the profile. Each drag is one Undo step; adding and deleting are
each one step too, and Undo/Redo restore both the curve geometry and the underlying profile. The
read-only curve previews in AI Coach and Recommendation are unaffected.

`CurveChart.curve_geometry_changed` carries both y values and normalized x positions, while
`curve_structure_changed` identifies a manual insertion or deletion. `CalibrationPage` applies those
edits to the exportable profile and keeps its history in sync. `tests/test_calibration_chart_edit.py`
(37 tests) covers dragging non-bend dots, x/y constraints, zero-gain points, menu availability and
actions, profile/export changes, and Undo. `tests/test_calibration_sample_rate.py` covers the rate
choices, precise timer intervals, persisted selection, active-session retuning, repaint throttling,
Python fallback retention of a 12-second 1 kHz trial, and strict enforcement of the sample cap while
preserving the click endpoint.

## Combo boxes always show a chevron (rev88)

`styles.py` restyles `QComboBox::drop-down` so the field ends flush at the right edge, and on the styles this app runs on that also removes Qt's own arrow — so every combo box (Provider, Model, Mode, Theme, the Capture page's display/preset/FPS fields) looked like a plain text box. The editable Model field was the worst case, because it *is* a text box until you click it.

`widgets.ComboField` is a `QComboBox` that paints the chevron itself, in the drop-down strip the stylesheet reserves (`ARROW_WIDTH = 22`, matching `QComboBox::drop-down { width: 22px }`), using the `chevron_down` glyph in the active theme's text colour (`text3` when disabled). Painting in `paintEvent` means it repaints on theme switches without any registration step. `QComboBox::down-arrow { image: none; width: 0; height: 0; }` stops the style from drawing a second arrow on platforms where it still would. All fifteen combo boxes in the app are `ComboField` instances, and `tests/test_combo_arrow.py` parses the page modules to make sure no bare `QComboBox()` comes back.

## The model list in Settings (rev87)

The model name used to be a plain text field, so a name that had been typed once and then cleared had to be recalled from memory. `SettingsPage._build_ai_card` now presents it as an **editable** `QComboBox`: pick from a list, or type (a model that is not installed yet, or a tag this endpoint spells differently).

The list is built by `_fill_model_choices()` from three sources, in this order, deduplicated case-insensitively:

1. the **saved** name (`ai/model`), so the configured model is never missing from its own list;
2. the names the **provider reported** last time (`ai/model_list` in `QSettings`), so the list survives a restart;
3. `ai_chat.suggested_text_models()` — labelled `(suggested, not installed)`, because a name in a list is not a promise that it is installed.

Each item stores `{"name", "source"}` as its data, so a label such as `phi4-mini (suggested, not installed)` saves the plain `phi4-mini`; `_model_name()` returns the real name, or exactly what was typed. Picking an item saves immediately (`textActivated`); typing is committed on focus-out, as before.

`ai_chat.list_models(base_url, provider)` fetches the real list: Ollama's `/api/tags` for local, an OpenAI-compatible `/models` (with `Bearer` only when a key exists) for online, both parsed leniently (`name`/`model`/`id`, or a bare list) and sorted. `_ModelListThread` runs it off the UI thread; the card's refresh button calls it, and `SettingsPage.showEvent` calls it once when the page is first opened and nothing is cached — so opening Settings is enough to get a usable list. Failures are reported in the card's status line and never clear the names already offered. `SettingsPage.shutdown()` (called from `MainWindow.closeEvent`) waits for the request so the page is not torn out from under a running thread.

## Spoken replies: volume default and guidance (rev85)

Speech volume is `tts/volume` in `QSettings`, range 0–100, and **defaults to 100**. The default is made explicit rather than implied: `AIChatPage.ensure_volume_default` writes `VOLUME_DEFAULT` (`VOLUME_MAX`) the first time the app starts with no stored value, and the slider is set to it before `refresh_from_settings` restores anything. A stored value is never overwritten — including `0`, which is a deliberate mute — and a corrupt non-numeric value falls back to 100 instead of silencing the coach.

Read aloud, **Stop speaking** and the reply text size live in the AI Coach tab's own Settings block. Settings has no Replies card: it duplicated all three against the same saved values, so rev86 removed it along with the two signals that existed only to keep the copies in step (`SettingsPage.stop_speaking_requested`, `SettingsPage.text_size_changed`, and `AIChatPage.replies_changed` / `set_text_size`). The one control Settings keeps is the **Speak responses aloud** switch under Voice, which shares `ui/tts_enabled` with the chat's **Read replies aloud**; `SettingsPage.refresh_voice_state()` keeps that copy in step when the chat changes it.

`SettingsPage._build_voice_card` carries `volume_note` under **Speak responses aloud**: it states that the app already asks the engine for 100% and points at the places that can still be quiet — Windows' own volume and Volume mixer (to lift Vantage above the game), and Sound → More sound settings → Communications → "Do nothing", so Windows stops ducking other audio when it detects voice chat. The note makes no promise about output the app does not control, and its tooltip points back at the in-app slider.

There is no post-synthesis gain stage: the previous loud-mode attempt (rev84) was withdrawn at the user's request, so speech goes straight to pyttsx3/SAPI5 or System.Speech at the stored percentage.

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
