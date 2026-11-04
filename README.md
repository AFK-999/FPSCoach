# CurveCoach — Windows desktop foundation

The repository, executables and settings retain their existing FPSCoach identifiers for compatibility.

A lightweight, local-first foundation for an FPS movement/sensitivity coach. It combines a PySide6 beginner interface with a native C++ core for Desktop Duplication capture and explainable cursor-movement analysis.

> **Fair-play boundary:** this project is a coaching/calibration tool, not an aiming tool. It does not inspect game memory, identify targets, inject code, hook or synthesize input, compensate recoil, or change settings automatically.

## What is included

- **Startup and wordmark** — the wordmark uses the bundled Inter font (SIL Open Font License; see `python/fps_coach/assets/fonts/Inter-OFL.txt`) with a system-font fallback. On launch, a brief, click-through multicolour ribbon effect fades in and out without delaying the interface. It runs only once per application launch and is omitted when Interface animations are off or Performance mode is enabled; it stops if animations are disabled while it is playing.
- **Theme-aware window and identity** — the app bar follows the selected light, dark, or system theme. Its inset rounded title strip and navigation bar follow the window corners, and compact minimize, full-screen and close controls sit on the right with accessible names and keyboard focus; F11 toggles full screen, Alt+Space opens the window menu, and Alt+F4 closes. Drag the title strip to move and use the outer edge to resize; native window-manager move/resize is used when available, with a basic fallback. Double-click the strip to maximize or restore. Windowed and maximized views have rounded corners where window-region masks are supported; full screen removes the mask and restores the preceding normal/maximized state on exit. The bot-and-curve logo is included as an SVG and Windows icon in `python/fps_coach/assets/branding/`. A custom frameless title bar may not reproduce every native OS snap or accessibility feature.

- **Beginner guide UI** with a measure → adjust → verify workflow.
- **Application Guide: Crosshair** — the main Guide presents Crosshair as numbered section 06, with matching numbered cards 06.1–06.5 explaining presets and preview, overlay and positioning, PNG/JSON import and export, optional Dynamic Magnification and game-rule cautions. Its **Open Crosshair** button navigates to the existing feature without enabling anything. The separate Calibration Guide contains five questions and a setup review, not Crosshair controls.
- **Desktop Duplication capture** (`IDXGIOutput1::DuplicateOutput`) on a dedicated C++ worker.
- **Python capture fallback** — without the native core, the Capture tab still streams a live preview through `mss` (up to 60 FPS), so a `FPSCOACH_SKIP_NATIVE` install or a developer machine is not left with an empty page.
- **Integrated-GPU support** by creating D3D11 on the selected output's owning adapter and defaulting to a bounded 640×360 GPU-side crop.
- **Latest-frame buffering** so a slow preview drops stale work instead of accumulating latency.
- **Recording on the Capture tab** — records the primary display in the background and samples the newest frame on a configurable analysis timer. Discard the file when you stop (default) or keep it in a folder you choose.
- **Display-change recovery** for `DXGI_ERROR_ACCESS_LOST`.
- **In-app 12-target calibration** offers foreground Windows raw relative input with a neutral virtual
  cursor, or legacy desktop-pointer sampling. Raw mode maps each delivered count to one virtual logical
  pixel until the arena boundary; it confines the OS cursor during capture. Esc, focus loss or navigation
  releases it; resume explicitly to restart the unfinished target. Device handles are used only to reject
  unknown or mixed input devices, not to read DPI. No device telemetry is implemented: configured DPI
  changes and physical sensor drift are not detected or corrected. Keep DPI fixed for a session. OS event
  batching, firmware, display scaling and games can differ, so this is not a verified 1:1 game path.
  Legacy desktop sampling offers requested rates of 125–1,000 Hz; OS/Qt scheduling can reduce the
  achieved rate. Raw mode processes delivered events rather than the timer. One real movement packet
  (or observed endpoint) can complete a hit: no intermediate samples are fabricated. A one-segment path
  cannot resolve unobserved corrections and its speed includes the time from target appearance to the
  observed movement; sparse windows show a warning and lower the heuristic confidence. A hit with no
  usable movement or an unusably close target is not counted: a fresh, reachable target starts the retry.
  Focus, device, capture or confinement loss still pauses raw input; resume
  explicitly. The curve updates after each completed target from
  captured cursor-speed samples and movement metrics. The graph selects the fewest **model control
  points** among sampled candidate positions within a 0.005 normalized distance of the sampled
  output-speed model. That tolerance does not bound the displayed sensitivity-gain graph; conversion
  to gain changes the geometry. This is a visual approximation, not exact preservation or a guarantee
  between samples. Straight models need only two points; curved models receive more where
  useful. The dots are model anchors, not raw recorded positions. A rolling window of the latest eight
  accepted targets supplies moving averages for overshoot, underreach, corrections and tremor; the
  heuristic recomputes after each accepted target, not continuously between samples. The graph has
  draggable dots, right-click add/delete, Undo/Redo, and explicit export; it starts empty until movement
  data exists. Exports retain the chosen points, but other applications may interpolate between them
  differently. Imported and manually edited curves are not simplified by this generation step.
- **Optional adaptive Calibration** — open the Calibration task settings and enable **Adaptive moving targets** before a run. It starts off each launch and is locked during a run. A local deterministic controller uses up to eight recent in-arena clicks, waits for three observations and changes at most one difficulty level per three clicks: higher visible-target accuracy makes targets smaller and faster; lower accuracy makes them larger and slower within bounds. Target motion uses logical pixels per second and pauses away from the task. The analyzer references the target center at click time; it does not reconstruct the target's full trajectory, so scores are not directly comparable with stationary-target runs. The existing larger click-acceptance tolerance remains separate from visible-target accuracy. Adaptive mode requires the updated analyzer; rebuild an older native extension if the option is disabled.
- **Personalized daily warm-up** — in AI Coach, choose **Generate daily warm-up** to request a routine based on miss counts from up to 30 saved Training sessions. An installed local Ollama model proposes 2–5 known drills with bounded durations. No misses means insufficient evidence; no heuristic or fabricated model answer replaces an unavailable model. The suggestion is shown in chat; start drills manually in Training. It is not an automatic daily reminder or a guaranteed improvement.
- **AI Coach session-based aim analysis** — the separate “Save in-app aim summaries for this session” control starts off each launch. When enabled during Training, completed drills add compact click-accuracy, hit-time, endpoint-error and moving-target pointer-event aggregates to the existing local training-history file; no raw pointer paths are saved. Mid-drill enablement is marked partial, and incomplete drills do not save the extra summary. Moving-target tracking reports the percentage of delivered pointer-move events on target, **not** time-on-target; stationary periods and game input are not measured. Coach displays observed values and differences from the accuracy and hit-time goals in Recommendations. Select Balanced, Fast flick, Tracking or Precision and explicitly request **Analyze sessions** to send up to 30 numeric saved session summaries, goals and the sampled baseline curve to verified loopback Ollama. The local model proposes bounded experimental gain factors, not a guaranteed optimum. The preview changes nothing until **Send to calibration**; game settings are never changed. Existing history without detailed summaries remains usable. Clear training history in Privacy to delete the saved summaries and stop collection. As with other local AI features, disable Ollama cloud and restart it if forwarding must be prevented.
- **Dynamic Magnification (Crosshair)** — Windows 10 version 2004+ only. Click the compact scope-icon **Dynamic Magnification** button to reveal its collapsed settings, enable the crosshair overlay, lock its position, and turn on Dynamic magnification to enlarge a circular area of the visible primary screen behind the crosshair. Adjust diameter (80–600 logical pixels) and zoom (1–5×); the lens is clipped at screen edges rather than moving the crosshair. Choose a keyboard key or physical mouse button and separately arm **Enable toggle key for this session** to toggle in the background while the overlay is active and locked. The selected key is polled without consuming it; fast taps may be missed and the same key can trigger game actions. The scope button only opens or closes settings; it does not toggle capture or change the armed key. The key works while the settings are collapsed. Magnification and the key arm both start off each launch; size, zoom and key choice persist separately from crosshair import/export. Turning off the overlay or editing its position stops magnification.

  This explicitly opt-in feature captures visible screen pixels (which can include game content) around the crosshair locally, requesting a 16 ms (62.5 Hz) capture timer, not game memory or a process hook. The key is sampled immediately on arming and then targets a 5 ms polling interval; a key held during arming must first be released. The first frame is captured on activation. These are scheduling targets, not guarantees: capture, painting and key polling share the GUI thread and can be delayed by slow screen grabs, system load, Windows compositing or game restrictions. Sustained 60 FPS and instant activation are not guaranteed, and this may use more CPU/GPU than the former 20 Hz target. It retains only the current image and does not save or upload it. The app requests Windows capture exclusion for its overlay and verifies the API setting before starting; if Windows reports error 8 on the transparent layered overlay, it retries with the layered style temporarily removed, restores the original style, and verifies again. If verification or the retry fails, capture stays off and the error is shown in the expanded settings. It stops if the overlay's native window changes. This may remove the overlay from recordings and cannot guarantee exclusion in every capture path. Turn it off if recursive visual feedback appears. Protected content, secure desktops and exclusive fullscreen may be black, stale or unavailable; borderless-windowed may work better. Check your game's and anti-cheat's rules before use: magnification may be prohibited or considered an advantage. No Windows/game or anti-cheat compatibility is guaranteed.
- **Dynamic performance curve (Recommendation and Training)** — in Recommendation, select the compact square bot-icon button (tooltip **Dynamic Performance Curve**) to flip the Suggested curve card to the performance panel; use **Back to Suggested curve** to return. The icon-only button uses the Dashboard Start button’s metallic gold-and-graphite styling. **Generate recommendation** remains at the bottom of the recommendation summary card. The two manual preset buttons remain in AI Coach. The Recommendation and Training panels share one session engine that also receives Calibration telemetry; switching the card view does not enable or disable collection. The flip is skipped when Interface animations are off or Performance mode is enabled. Edit the example accuracy goal (1–100%) and target-to-hit time goal (50–5,000 ms), then explicitly enable local AI analysis. The toggle and goals are session-only and start off at launch. During the in-app calibration task and training drills, the app records up to 32 recent click attempts per source, their individual app-event timings, visible-target hit/miss, endpoint error, and observed pointer/virtual-cursor travel speed and path efficiency when movement is available. Accuracy counts clicks within the visibly drawn target; calibration may accept a wider 1.65× radius to finish its separate movement trial. Hit time is target shown, calibration trial restarted, or analysis enabled mid-target to hit, using a monotonic app-event clock—not hardware timestamps, pure reaction time, an in-game crosshair, or a guarantee of timing precision. Pointer speed is in desktop or bounded virtual pixels per second and is not physical distance. Moving training targets and raw/desktop paths differ; the model receives source labels rather than merging them. Only in-arena clicks are counted. Re-entering calibration restarts the unfinished target; training discards movement paths across an arena exit instead of inventing an outside trajectory. At least three attempts, including one hit, are needed.

  The selected installed local Ollama model receives the numerical goals, recent per-attempt summaries (including observed path length and movement-event count), source context and sampled baseline curve; it is asked for 3–16 numeric speed-dependent gain factors, not a named preset. When the base is the unchanged, generated calibration curve and the input paths match, the request also includes that run's 95th-percentile in-app speed reference and approximate per-attempt mean-speed fractions (clipped at 1). Mean path speed is not instantaneous speed, and this is not physical or game-speed calibration. Imported, edited and neutral curves have no verified speed mapping; training and different paths are not mapped to it. Factors must be finite, ordered and within ±10%; invalid/unavailable models produce an error, not a heuristic substitute. Model latency varies: snapshots are debounced and coalesced with one in flight, so the preview is asynchronous, not instantaneous or a hard real-time loop. The read-only dynamic previews have square plot areas in Recommendation and in Training, which also retains a compact square live monitor. Calibration shows only its editable Sensitivity curve, with import, export and undo/redo. Its task still contributes data when analysis is enabled. Current data and a current base are required to confirm; changes during calibration cannot be confirmed until the task finishes. If no imported or calibrated curve exists, training starts from a labeled neutral 1× base. Confirm changes only the in-app Calibration curve; export is separate, and the game, device and OS settings are not changed. Suggestions are experiments, not a guarantee or proof of a unique optimum.

  Data is collected only while enabled and only within the two in-app tasks; the per-attempt window is session memory, not saved history, and is cleared when disabled or when training/calibration history is cleared. The existing training analytics store remains separate. Requests use direct loopback Ollama with known cloud models and remote metadata rejected, but a local daemon can forward data; disable Ollama cloud features and restart it if offline privacy is required. In-flight requests can finish after disabling and their results are ignored. The feature does not download a model, read game telemetry, use screen capture, or send raw cursor coordinates.
- **Manual curve presets (AI Coach → Curve tuning)** — the compact **Optimize for Fast Flick** and **Precision for Micro Aim** buttons preview distinct manual adjustments to an existing calibrated or imported curve. They are separate from the model-based dynamic analysis; neither button applies a curve automatically. Review the solid proposal against the dashed baseline and choose **Send to calibration** to confirm the in-app change. Export separately; no game or device setting is changed. Other topic-specific suggestions remain available.
- **Optional calibration guide (Dashboard)** — use the muted-gold **Start** button next to the Dashboard title to open the guide, or skip it. The guide follows the current application theme, with a rounded title strip and compact close control that discards uncommitted answers. Its small book icon and click pulse are contained in the button; the pulse runs only when animations are enabled and the UI is not in Performance mode. Five optional questions are followed by a setup review with exact grip and flick-motion labels and an illustrative hand, wrist and forearm viewed from above. Vivid gold grip-area and teal motion-area highlights pulse directly on the illustration, following distinct finger, palm, wrist and forearm contours rather than circular markers; overlapping finger areas retain both colors. When animations are disabled or Performance mode is on, the highlights remain visible without motion. The same generic hand is used for every grip: it is not a depiction of the user’s actual posture, contact or tracked anatomy. Unanswered or unknown selections do not produce invented highlights. For Shoulder / Hybrid the shoulder is not shown; the forearm highlight is only a schematic proxy. An explanatory flow shows how an optional AI suggestion can be previewed and then confirmed; it does not itself apply a curve or start capture. Grip cards use stylized static illustrations with glowing arrows; they are examples, not tracked anatomy. Choose issues, grip, motion, pad and optional setup. The current-setup question has separate manually entered mouse, game, sensitivity and DPI cards, including unknown options. No game settings or device DPI are read automatically. Back, Skip, Cancel and Finish retain their session-only behavior for guide answers. The Crosshair feature is separate from this wizard; the main Application Guide has a structured Crosshair feature guide and a button opening the existing Crosshair tab. Crosshair edits take effect immediately and preferences are saved; position-edit mode, magnification and its key arm are session-only. Overlay and magnification use may be restricted by game rules. **Preview in AI Coach** on the review also completes the guide. **Finish and open Calibration** saves the answers and opens that tab without starting capture. General card guidance is immediate. With **Analyze selections with local AI** on (default for each guide opening), changes queue a local-model request after a short debounce; model inference can take time. An installed Ollama model and loopback Ollama server are required; the guide does not download models or use the configured online provider. If unavailable or invalid, it shows an error rather than inventing a suggestion. Current self-reports, including typed setup, and available numeric session metrics are sent to the loopback service. Disable Ollama cloud features (`OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud` in its server configuration) and restart Ollama if data must stay offline (see [Ollama's FAQ](https://docs.ollama.com/faq)): model-name and metadata checks reject known remote models, but cannot prove a local service will not forward data. Requests already in flight can finish after disabling AI or closing the guide. The model's factor is bounded to ±10%, is only an unverified experiment—not a unique or optimal sensitivity guarantee—and appears as a preview. Once current local analysis is ready, **Preview in AI Coach** reuses its guidance as a Coach message and, when **Read replies aloud** is enabled, queues it for speech; it makes no additional AI request. If speech is off, the message explains how to enable it for future replies. A curve preview needs an existing measured or imported curve; without one, Coach still shows the guidance and explains how to create a baseline. **Send to calibration** is a separate confirmation. No game or device settings are changed. If analysis is unavailable, toggle local analysis off and on to retry after fixing the model or service. Finished answers may accompany later text Coach requests, including to an online provider and enabled proactive coaching, even with In-Game Mode off. Live-vision text excludes answers, though preview frames can show the UI. Reopen the guide and choose **Clear saved answers**, or clear calibration history in Privacy; chat messages and edited drafts are separate. The analyzer and its measured scores remain independent of self-reports. See [research notes](docs/calibration-guide-research.md).

- **Custom Curve compatibility** for importing/exporting modern `.cc4` JSON profiles and legacy `.ccurve` text profiles, selected explicitly in the UI.
- **Curve graph axes** — all sensitivity charts use numbered input-velocity and sensitivity-multiplier axes. Generated curves use CurveCoach's normalized 0–100 input-velocity scale, not physical velocity or raw counts/ms. Imported curves show stored profile speed values as **profile units**; their interpretation depends on the source application's normalization settings. Hover a plotted point for up to 12 significant digits; tick labels are rounded. Before/after curves share one display scale; normalized curves from different runs can have different underlying speed references. Use the **graph-style selector** on the Calibration curve to switch every preview between CC3 and CC4 styles. The preference is remembered; switching styles alone does not modify a profile. In CC3 style, Calibration uses a shape-preserving line through every displayed horizontal profile point (repeated velocities are connected vertically), while Recommendation, Training and Coach retain an unweighted CC3-style B-spline approximation. In CC4 style, the graphs use a weighted B-spline approximation: control dots may lie off the line, and each current/previous curve uses its own weights. Neither style is verified Custom Curve driver behavior; the actual exported profile may render differently. These are profile/model control points, not every raw mouse sample. Exact interpolation, corner flags and device settings are not simulated. Scroll over the plot to zoom both axes, or scroll over an axis's numbered strip to zoom that axis. Wheel and touchpad zoom use small proportional steps; horizontal zoom follows the pointer, while vertical zoom scales from the lower edge and keeps both the current and comparison curves in view. A vertical zoom after a deliberate pan refits any cropped data. Drag empty plot space or an axis strip to pan; middle-click to reset. Each drawn curve ends at its last actual control point, even when the viewport or comparison curve extends farther right; no extra flat upper-speed segment is drawn. The view can extend beyond the data without changing the curve. In Calibration, open the settings icon beside the Sensitivity curve title to reveal the collapsed exact-point editor. Click a control dot or select any point in the Control point list, enter its exact input velocity and sensitivity multiplier, and press Apply (or Enter). Values are horizontal-profile units, use a decimal point, must be finite and nonnegative, and velocity must stay ordered between neighbors. New numeric edits are limited to velocity 1,000,000,000 and sensitivity 20; existing imported values above those limits can be preserved or reduced. In CC4 style, select a point (or right-click it and choose Edit point weight) and enter its weight from 0 to 1 alongside its velocity and multiplier. Apply commits the fields together; higher weight pulls the approximate preview toward that control. Lower input speeds generally correspond to micro aim, higher speeds to flicks, but a weight change is not a guaranteed improvement. Identical horizontal/vertical profiles stay linked; independent vertical data remains unchanged. Errors appear beside the fields, edits support Undo/Redo, and export is explicit. Fields and drag editing are disabled during active calibration; Recommendation, Training and Coach previews remain read-only. Export defaults to `.ccurve` in CC3 style and `.cc4` in CC4 style; extra legacy settings rows are preserved when importing and saving CC3. CC4 weights round-trip in `.cc4`; a confirmation warns before exporting CC4 weights or settings to CC3, which cannot store them. CC3-to-CC4 conversion also warns that legacy settings may not transfer. Exported generated 0–100 velocity values are not measurements in counts/ms and need review/scaling in Custom Curve before use.
- **In-Game Mode (Settings → Advanced)** — off at every launch. Turn it on for a session, pick your game, and enter your DPI and in-game sensitivity; CurveCoach converts that to cm/360°, shows what its recommendation means in that game's units, and can ask the coach for a specific value. It never reads or changes the game.
- **Live vision (AI Coach)** — off at every launch. Turn it on and the coach watches the live capture preview: it samples the preview into short clips (4 frames half a second apart by default), measures what moved between them, and comments on the movement and the setup. **Look now** takes a clip on demand. It is instructed never to analyse targets, enemies, or aim. Needs a vision model (for example `gemma3:4b`); the card says so before you start.
- **Optional AI coach** using local Ollama models (defaults to `qwen3:1.7b`, with `phi4-mini` as an option) or a user-configured OpenAI-compatible online API. Settings → AI coach lists the models your provider actually offers, so a model can be re-picked from a dropdown instead of retyped. User messages appear in dark, rounded bubbles on the right, and ordinary coach replies in rounded bubbles on the left; automatic coach and live-vision updates appear as muted text without bubbles. Speaker labels are bold, while message text remains selectable. After each accepted calibration target, the coach receives the current session's rolling scores and model points as provisional context; old in-flight responses are discarded when that context changes. Numeric AI proposals use the current measured curve and appear as before/after graphs; sending them to Calibration requires your choice after the run finishes. Manual preset chips are explicitly labeled and are not data-derived recommendations. AI text can still be wrong.
- **Speak a question** in AI Coach — click the microphone to start one-shot voice input. The status shows preparation, listening while the microphone is open, and speech processing where applicable. The button is unavailable until the one-shot attempt finishes; missing optional voice dependencies are explained in chat.
- **Optional voice commands** — in Settings → Voice, enable voice commands and check the listener status. It uses continuous local Vosk recognition and may automatically install missing voice packages or download a model during setup. It listens while enabled; commands act only while CurveCoach is focused. Complete command phrases work without a wake word, so nearby speech can be misheard. A configured wake word may be prefixed, such as “Coach, open calibration settings.” An unmatched wake-word phrase or “Ask coach …” sends recognized text and normal chat context to the configured AI provider. Commands are ignored while the separate one-shot chat mic is active and, except “Stop speaking,” while the app is speaking. Avoid using both microphones at once. Turn commands off when not wanted.
- **Portable Python reference analyzer** so UI/model work can be previewed without the Windows extension.

## Voice command examples

Voice commands are opt-in in **Settings → Voice** and the preference is remembered. These are whole-phrase actions, not unrestricted natural-language automation:

| Say | Result |
| --- | --- |
| “Open calibration settings” / “Close calibration settings” | Open or close the Input Path panel. It stays locked during a calibration run. |
| “Open training settings” / “Open coach settings” | Open the relevant in-page settings; “close … settings” closes them. |
| “Open voice settings” / “Open AI settings” / “Open advanced settings” | Navigate to that Settings section without changing its controls. |
| “Open calibration guide” | Open the optional guide, provided calibration is not running. |
| “Next guide question” / “Previous guide step” / “Skip guide question” | Navigate the open guide. Skip clears that question's answers; finishing the review still requires its on-screen button. |
| “Show sensitivity curve” / “Open curve tuning” | Show the Calibration chart or the AI Coach curve panel. |
| “Explain my calibration” / “Ask coach how can I improve?” | Send the stated question and normal context to the configured coach provider. |
| “Stop speaking” | Clear queued speech; the sentence currently being read may finish. |

Existing page navigation, explicit start/reset calibration, raw resume, coach-strip visibility and manual curve-preview commands remain available. A voice question does not replace an unsent chat draft or interrupt a pending request. No voice action starts recording, enables Live vision, applies an AI curve, or changes game settings. Spoken recognition is not guaranteed; review effects and disable the listener when unintended speech might be heard. The separate **AI Coach microphone button** is one-shot question dictation, not this command listener; if SpeechRecognition is installed it uses Google's online speech recognition, otherwise it can use Vosk.

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
3. Optionally open the **Calibration Guide** on the Dashboard. Then open **Calibration**, use the settings icon beside **Calibration task** to expand the inline **Input Path** panel, choose an input path, and click all 12 targets. Click the icon again to collapse the panel. The two existing paths are Windows raw with the in-app virtual cursor, or Desktop pointer with the natural OS pointer; the desktop sampling-rate target is also there. During a run the settings and panel toggle are locked, so the task area does not shift. On Windows, raw mode uses a neutral virtual cursor rather than your game settings; press Esc to release capture. If no raw movement arrives, the task displays a warning; press Esc, then Reset to unlock Input Path and try Desktop pointer if needed. The curve graph does not stretch when the settings panel opens. Leaving the page pauses capture; after returning, click **Resume raw input** to restart the unfinished target. The graph and AI Coach context refresh after each accepted target.
4. Review **Recommendation**. Its graph uses a normalized model input domain and normalized recommended output; its model points are not raw cursor positions. The point count adapts to the model's shape.
5. Return to **Calibration** to import or export **Custom Curve 4 (.cc4)** or **Custom Curve 3 (.ccurve)** explicitly. Export creates a profile file; it never applies one.
6. Optionally open **AI Coach** to discuss the current session with a local or online model. Test over several sessions and revert if comfort or consistency declines.

### Live vision

The AI Coach tab's **Live vision** panel samples the Capture preview and sends short clips of
ordered frames to your configured model. It is **hidden until you enable it** — Settings (the gear in the chat card) →
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
input. FPS Coach sends sampled images and timing descriptions; how the provider processes those
images depends on its implementation and is not guaranteed to be native video. `gemma3:4b` also works. Every
frame's offset is spelled out in the prompt (`Frame times, oldest first: 0.0 s, 0.5 s, 1.0 s`),
because Ollama gives the model images without temporal position ids.

Frames are sent to your configured endpoint, which can be remote even for Ollama. A loopback
service can also forward data; disable Ollama cloud and restart it for local-only operation.
A provider may retain frames. **Look now** sends at most one clip for inference without starting Watch live;
turning Watch live off cannot recall a request already in flight. See `docs/PRIVACY.md`.

**Fair play:** The crosshair, screen magnifier, screen capture and vision features may be restricted during games or tournaments; no publisher has approved this build and no anti-cheat outcome is guaranteed. The safest option is to close CurveCoach before launching a protected game. See [the feature-by-feature review](docs/FAIR_PLAY_REVIEW.md).

Screen capture and calibration are independent in this foundation: pixels are available for future, policy-compliant local context models, while the current curve model uses only the explicit calibration movements.

## AI chat setup

The chat interface is optional and uses the Python standard library—no AI SDK is required.

For local inference, install [Ollama](https://ollama.com), then pull one small model:

```powershell
ollama pull qwen3:1.7b
# or
ollama pull phi4-mini
```

Keep **Provider: Local Ollama**, use `http://127.0.0.1:11434`, and select the matching model tag. Requests go to that server, which could itself forward data; disable Ollama cloud and restart it for local-only use.

The current Settings UI has no API-key field, so an authenticated hosted-provider setup is not available through this build. A configured Ollama endpoint can be remote, and a local server can forward requests; review the endpoint and its privacy settings. Text mode sends the conversation, numeric calibration summary, and up to 32 horizontal speed/gain points from the active curve to the configured provider; raw cursor samples and screen frames are not included. Live vision is a separate opt-in feature that sends preview frames while watching is on or when you explicitly select Look now.

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
- Calibration processes either foreground Windows raw relative input or legacy desktop cursor positions only during the in-app task. It does not read a game's input stream, query hardware DPI or detect physical sensor drift.
- `.ccurve` and `.cc4` cover Custom Curve compatibility only; other vendor/game formats still require explicit, reviewed adapters.
- Chat quality and hardware requirements depend on the chosen model/provider. FPS Coach does not download model weights automatically.
- The current pixel path is capture infrastructure only; it does not perform target or enemy analysis.
