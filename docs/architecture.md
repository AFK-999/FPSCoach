# Architecture

## Crosshair feature in the Application Guide (rev147)

The Calibration Guide has five optional questions followed directly by the setup review. It no longer embeds or reparents the Crosshair page. The main Application Guide displays Crosshair as section 06, followed by numbered subsection cards 06.1–06.5 using the same badge, glyph, and card layout as the other guide steps. These cover preview and customization, overlay and position, import/export, Dynamic Magnification, and game/anti-cheat rules. Its Open Crosshair button selects the existing page without changing the overlay, capture, key polling, or settings. Crosshair settings continue to apply immediately and persist as implemented; position edit, magnification and key arming remain session-only. The guide warns that game rules can restrict overlays and that no ban-safety guarantee is provided.

## Voice commands (rev113)

The optional continuous Vosk listener emits finalized phrases to the main UI thread. Voice settings persist; Vosk recognition is local once installed, while missing dependencies or the model may be downloaded during setup. Command phrases are full-pattern matches with optional polite wording/wake word, and specific settings/guide actions are matched before broad navigation. An explicit “ask …” is chat dictation; an unmatched configured wake-word phrase is also sent as chat. Dictation and the explicit calibration-explanation action use the existing configured provider and context, preserve an unsent draft, and do not interrupt a pending chat request. Commands can operate only while the app or its guide is the active window; a different modal dialog blocks them. They are ignored while the one-shot chat mic is active and, except for “stop speaking,” while the app reports an utterance in progress; echo and speech recognition are not foolproof. The on-screen one-shot mic remains question dictation, not a command listener; it may use online Google recognition if SpeechRecognition is installed.

Voice actions reveal existing Calibration, Training and Coach settings or Settings sections, operate the existing guide's next/back/explicit skip controls, navigate and request chat/curve previews. Guide validation and review confirmation are not bypassed; skip clears a question like its on-screen button. Locked calibration controls stay locked, moving away pauses raw capture, and failed/unavailable listener status is visible in Settings. Voice does not start recording/live vision or apply an AI curve. Feedback is a transient overlay, not a new layout row that could move the calibration arena.

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

## Calibration task settings and setup-review motion (rev106)

The standard settings icon next to **Calibration task** expands and collapses a keyboard-accessible inline panel (rev108 supersedes the rev106 separate dialog). It contains the existing input-path selector, explanations, DPI telemetry warning and desktop sample-rate target. There are two paths, not four: Windows foreground raw packets drive an in-app virtual cursor, while desktop mode samples the natural OS pointer. The same combo signals and sample-rate preference are retained. Controls and the panel toggle are locked during an active run so opening or closing the panel cannot reflow the calibration arena while it is measuring. The graph column sizes from the viewport instead of the adjacent task column; a reserved scrollbar width prevents horizontal jumps when settings expand. It remains responsive to window resizing. The panel does not add raw capture, read DPI, or change game settings.

The Dashboard guide launcher is a muted-gold **Start** button beside the title, with a small book icon (rev109 supersedes the separate rev107 block). Its painted click pulse runs inside a fixed layout footprint before opening the guide; repeated clicks do not queue opens, and hiding the launcher cancels a pending animation. Both `ui/animations` and `ui/mode` are respected: disabled animations or Performance mode open immediately. The review scene includes a bundled, AI-generated five-finger hand illustration with joint details and vivid gold grip/teal flick-motion highlights traced to finger, palm, wrist and forearm silhouettes (including aligned vector fallback highlights if the asset is missing). Adjacent masks are combined before glow rendering to avoid bright circular seams at the joints. The highlights pulse only while visible, when animations and UI mode allow; otherwise they remain static. Unknown or skipped selections do not highlight a region. Finger motion uses narrower teal overlays to keep overlapping gold grip areas visible. The top-down view shows hand, wrist and forearm, but uses the same illustrative schematic for every grip, not tracked or a claim about actual posture or contact. The Shoulder / Hybrid forearm highlight is only a proxy; the shoulder itself is not shown and the UI says so. Labels still reflect the exact selections. A one-shot progress transition explains the optional answers → local AI preview → confirmation sequence; when animations are disabled it displays the completed static flow. It is purely explanatory, changes no settings or curve, and never starts capture. A guide AI suggestion requires the existing explicit **Send to calibration** confirmation; ordinary calibration still updates its provisional curve per target.

## Guide preview feedback (rev107)

The review button is enabled only for the latest valid local AI result; pending, failed, disabled and stale analyses cannot silently use a heuristic. The existing guide result carries a bounded factor and model-written guidance. When the user previews, the dashboard commits the answers and emits the proposal to `MainWindow`, which opens AI Coach. `preview_guide_proposal()` uses the existing current Calibration curve (or an available fallback baseline) to display a before/after overlay; it also appends the already-generated guidance and explicit uncertainty/confirmation information to the transcript, then emits the normal `assistant_said` signal. The signal feeds the existing speech service only if spoken replies are enabled. If off, an automatic transcript note points to the speech setting; enabling it affects future replies, and the app does not silently override the saved preference. Speech failures use the existing visible TTS error path. Previewing performs no additional AI/provider request and does not add the guide result to provider-bound chat history. A later ordinary chat request may still include committed guide answers as documented in Privacy.

When no curve exists, the user still gets the local model's guidance, but the transcript explains that no graph can be made until calibration or import; invalid and in-progress cases similarly explain failure and remove any stale actionable preview. No model text is parsed as an adjustment directive on this path. Showing or speaking never sends a curve, modifies game/device settings or begins capture; **Send to calibration** remains explicit confirmation. Disabling local analysis, changing answers or receiving stale results prevents use of an old proposal. Retrying after an unavailable model is possible by toggling local analysis off and on.

## Calibration sampling and graph editing (rev89)

The legacy desktop-pointer input path offers requested cursor-sampling targets of 125, 250, 500, and
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
AI Coach and Recommendation charts remain read-only; rev90 adds live/data-driven source curves and visual AI proposal overlays in the Coach panel.

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

`SettingsPage._build_voice_card` carries `volume_note` under **Speak responses aloud**: it states that the app already asks the engine for 100% and points at the places that can still be quiet — Windows' own volume and Volume mixer (to lift CurveCoach (which may appear as FPSCoach) above the game), and Sound → More sound settings → Communications → "Do nothing", so Windows stops ducking other audio when it detects voice chat. The note makes no promise about output the app does not control, and its tooltip points back at the in-app slider.

There is no post-synthesis gain stage: the previous loud-mode attempt (rev84) was withdrawn at the user's request, so speech goes straight to pyttsx3/SAPI5 or System.Speech at the stored percentage.

## Visually balanced calibration points (rev96)

Rev95's analytic 0.1% optimizer and its associated metadata were fully reverted to rev94 before this implementation. The recommendation remains the same local heuristic `f(x) = x^gamma * (1 - 0.12*tremor*(1-x))`, shaped by recent movement metrics. Rev96 samples that model at 129 evenly spaced normalized x positions and up to 31 additional positions influenced by the recent speed quantiles. The empirical candidates use the 95th-percentile nearest-rank speed reference and the rev94 80/20 domain/quantile blend; this keeps a domain-wide reference even when speeds repeat or an outlier is present. Speeds above the reference are clipped for candidate placement only, not discarded from the movement metrics. Small samples can still use their maximum as the reference.

A dynamic-programming search selects the fewest candidate anchors for which each sampled output-speed model position lies at most `0.005` normalized model-space distance from its corresponding line segment. Since rev132 the graph displays converted sensitivity gain rather than output speed; that tolerance is not a bound on the displayed gain graph. This uses the same point-to-segment metric and tolerance as the existing chart-handle selection. When several selections need the same minimum count, the search also favors a smaller worst sampled deviation, reducing awkwardly crowded points. Straight models need two endpoints; curved models use more points where shape warrants them. Counts are not fixed or forced to grow with sample count. The finite candidate-set minimum is **not** a claim of global minimality over arbitrary real-valued positions. The reference points are sampled model values, not raw input samples.

The tolerance is a visual sampling heuristic: it is not exact equality, a continuous-domain error bound between the reference positions, or evidence that the recommendation is accurate for a player. As of rev134, CurveCoach previews an unweighted CC3-inspired B-spline through control-point influences rather than drawing straight segments; it is not verified as Custom Curve 3 interpolation. Generated `.ccurve`/`.cc4` profiles retain their anchor positions, but convert output to gain, and other applications may interpolate differently. No exported-curve interpolation guarantee is made. Imported, manually edited and AI-adjusted profiles are not simplified by the generator. The tooltip explains the scope and is reset for edits/imports and restored on undo. A native extension with an older `curve_distribution_version` (including rev95's version 3) falls back to the current Python analyzer while retaining native capture.

## Calibration graph domain coverage (rev94; point selection superseded by rev96)

Rev94 addressed clustering caused by normalizing all speed quantiles to the single maximum. Its robust 95th-percentile speed reference remains for empirical candidates, but its sample-count-driven point count and mandatory 80/20 blend for *every selected anchor* are replaced by the visual selection above. A genuinely linear recommendation is still not artificially bent.

## Rolling-window calibration (rev93)

The native analyzer and Python fallback retain the latest eight **accepted** trial metrics and their valid cursor-speed samples in a bounded per-session window. Rejected trials do not enter the window; when it fills, each new accepted trial evicts the oldest metrics and speeds. Reset and cancellation clear it. `trial_count` remains the total accepted targets in the session, while `window_trial_count` and `window_capacity` identify the evidence used by the current recommendation. `captured_sample_count` now counts movement segments in that window, not the full session.

Each accepted trial contributes equally to the moving mean of overshoot, underreach, corrections, tremor, and the other summary metrics. The existing bounded, explainable sensitivity and power-curve formulas consume these means directly; their coefficients remain hand-chosen heuristics rather than learned weights. The same recent window supplies the movement metrics and speed-quantile candidates; the visual point count depends on the resulting model shape. The graph and the underlying recommendation refresh after each accepted target, not after every cursor sample; a finished run is one Undo step and cancel restores the prior curve. The user can review and export; nothing is applied to the game.

Confidence is a heuristic based on the number of trials in the window divided by its eight-trial capacity. The native analyzer additionally considers overshoot consistency inside that window; the Python fallback retains its 0.75 ceiling. Because their per-trial correction and tremor calculations differ, native and fallback results need not be identical. If an older native extension lacks the window metadata, `CoreBackend` retains native capture where available but uses the rolling Python analyzer instead; rebuilding the native extension restores native analysis.

## Data-derived calibration curves and AI Coach previews (rev90, updated in rev93)

The analyzer retains non-stationary cursor-speed samples for the most recent eight accepted trials in memory. After each completed target, both the native analyzer and Python fallback recompute a provisional recommendation from those speeds and the window-averaged path/overshoot/underreach/correction/tremor metrics. The Calibration graph is empty before there is captured movement; it updates during the 12-target task and restores its pre-session curve if the user cancels.

Rev96 chooses the fewest finite-candidate anchors needed to preserve sampled model details within a visual chart-space tolerance, as described above. The dots are not raw cursor samples. The measured movement metrics shape the monotonic output curve, which ends at `(1, 1)`. The graph is temporarily read-only while the live estimate changes; the final recommendation becomes the editable/exportable curve.

The AI Coach's adjacent **Curve tuning** panel displays the full calibrated or imported curve. Calibration recommendations and recognized AI curve directives are drawn as before/after overlays (solid proposed curve, dashed prior curve); AI proposals do not switch pages or change the Calibration curve until the user clicks **Send to calibration**. User messages appear in right-aligned bubbles, regular coach messages in left-aligned bubbles, and automated notes as muted text. Speaker names are bold, message text is escaped before rich-text rendering, and existing messages are redrawn when the theme changes. The prompt requests a numeric sensitivity directive; supported numeric changes can be previewed on the measured baseline. Preset-shape directives are not automatically applied.

Raw cursor samples remain local. Text-coaching requests may include the numeric calibration summary and up to 32 horizontal speed/gain points from the active profile, but do not include the raw cursor samples. See `docs/PRIVACY.md`.

## Movement model

Calibration gives the native analyzer timestamped active-path positions (desktop cursor coordinates or bounded raw virtual-cursor coordinates) plus each known target. Per trial it extracts:

- direct/path distance ratio (path efficiency),
- maximum forward projection beyond the target (overshoot),
- endpoint projection (underreach),
- backward segments and lateral sign changes (corrections),
- perpendicular RMS movement and reversals (small oscillation score),
- endpoint error, duration, and mean speed.

The recommendation model is deliberately small and explainable. Rolling-window means adjust a bounded power curve, a low-speed damping term, and a conservative sensitivity multiplier. Candidate positions cover the domain and include empirical speed quantiles of the same recent window; the visual simplifier determines point count from the sampled shape rather than sample count. Curve points are forced to remain monotonic and to finish at `(1, 1)`; a straight or near-straight curve can be legitimate. Confidence is a window-size heuristic; the native analyzer also considers overshoot consistency as described above.

This is a starting recommendation, not a trained medical assessment or an automatic control system.

## Optional calibration guide (rev101; moved to Dashboard in rev102)

The optional modal guide has five questions and then a setup review and never gates the existing 12-target task or curve editor. It has multi-select aiming issues; exclusive grip, flick and relative mousepad choices; optional usable width and user-typed setup. Continue allows blanks, Skip clears the current question, Back retains local answers, Cancel discards uncommitted answers; Finish or Preview in AI Coach on the review commits a normalized, session-only snapshot. Finish and open Calibration commits and navigates to the tab but does not start capture. The Dashboard is the sole guide entry point and the flick-motion choice appears only inside it; Not sure makes no inference from cursor samples. The grip cards use bundled stylized static illustrations with glowing arrows; motion cards retain static images. None are video, anatomical measurements or live tracking. The Dashboard guide control is a Start button with a golden book icon, accessible name and tooltip. Inputs are bounded, optional positive finite values (DPI whole, 1–64000; sensitivity 0.00001–10000; usable width 0.1–5000 mm); game and mouse names are bounded free text. Do not infer cm/360 from DPI and sensitivity alone.

The local card guidance is static and clearly distinguished from AI. Evidence and limitations are documented in [calibration-guide-research.md](calibration-guide-research.md). Coach context includes selected labels, relevant coaching instructions and quoted user-entered setup; the text-provider context cap is 6,500 characters to leave room for both guide answers and current measured context; arbitrary typed names are identified as untrusted data, although prompting is not a guarantee. This context is independent of the Settings In-Game Mode toggle, does not change the movement analyzer or measured scores, and is excluded from Live vision text (screenshots may still capture it). The separate discussion draft does not itself send a request; existing user-initiated and enabled proactive text requests can include finished answers. Guide-local AI requests can occur while editing, as described below. On changes, context revision invalidates in-flight results and existing AI previews, and an untouched generated guide draft is discarded; user-edited drafts are preserved. Clear saved answers inside the reopened guide or Privacy’s calibration-history action clears session answers; chat messages are separate. No game or device inspection is performed.

## Self-reported aiming motion (rev100; guide-only in rev102)

The optional guide's flick-motion step offers Wrist, Fingers, Arm, and Shoulder / Hybrid, plus Not sure. It shows non-AI general guidance on flick range/speed, tracking and possible fatigue. This is user self-report, not measured joint movement or a diagnosis; multiple joints may contribute, and effects vary. The choice exists only in the guide on the Dashboard, is session-only and can be cleared on the Dashboard or through Privacy's clear-calibration action; existing chat content and edited drafts are separate.

The selection does not enter the native/fallback movement analyzer, score formulas, curve generation, confidence or export. It is qualified context in text-coaching requests. Changing a guide selection can trigger the separate loopback-model request below; opening the separate discussion draft does not itself send that draft. Existing enabled proactive text coaching can include the context. Live-vision text excludes the self-report, although captured preview frames could show the guide. Changes invalidate in-flight text replies and old AI proposals; an untouched guide draft is discarded, while user-edited drafts are preserved. Generative text can still be wrong.

## Setup cards and final review (rev105)

The Dashboard's guide card contains only the centered clickable book logo. Reopen the guide for its saved-answer discussion and clearing actions; cancelling that dialog discards uncommitted questionnaire answers. Crosshair settings are edited separately on the Crosshair tab. The fifth question presents separate mouse/game, in-game sensitivity and DPI cards, with optional unknown controls and manual ±100 DPI entry buttons. Neither the controls nor the preview query hardware, identify a mouse, read or modify game settings, or infer cm/360 without the necessary game-specific information.

After question five (including Skip), a sixth, read-only setup review shows the normalized current session answers, including game, sensitivity, DPI, usable pad width, mouse, pad size and reported issues. Separate highlighted callouts display the exact grip and primary flick-motion labels selected, distinguishing explicit Not sure from a skipped question. The central monitor, mousepad, mouse and arm are schematic; colored region highlights indicate self-reported categories, not actual posture, contact or tracked anatomy. Back returns to editable answers and refreshes the review; Finish commits, Cancel discards, and Finish and open Calibration navigates without initiating capture. The separate AI curve-preview button remains a deliberate action, available from review only.

## Local guide AI proposals (rev104)

Each guide opening starts with its local-analysis checkbox on. A selection or setup change immediately invalidates the old proposal and queues a debounced request to the configured loopback Ollama server on a daemon worker. Only one request is active; revision tokens discard stale, disabled or closed-dialog results. Closing the dialog does not forcibly interrupt an in-flight HTTP request. The request contains normalized self-reported answers (including optional free text and setup) and a whitelist of current-session summary metrics if available; no raw cursor samples are included. General card guidance is still available without an AI model.

`guide_ai.py` permits only loopback HTTP, disables environment proxies and redirects, checks `/api/tags` and `/api/show`, rejects known cloud model tags and `remote_host`/`remote_model` metadata, and never pulls a model. Failed or unavailable models produce an error, not a fabricated result. A local service can lie or forward data; users requiring offline operation must disable Ollama cloud features in the server (`OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud`) and restart it. No app-level check can guarantee that another process will not forward data.

The model's response is untrusted JSON; factor must be finite and plausible and is limited to ±10%, with bounded plain-text guidance. This is a relative suggestion, not measured drift, a grip-specific optimum or an assurance of improvement. Every new selection invalidates the old proposal. The guide displays the numeric preview; Preview in AI Coach finishes the guide and displays a before/after overlay on an existing measured or imported curve. Send to calibration is a separate confirmation; no game setting is applied. Without a curve the user must calibrate or import first, then reopen the guide. Measured analysis and scores remain independent of self-reports. Model inference may be delayed and its text may be wrong.

## Foreground raw calibration and current-session coach updates (rev103)

Windows raw mode starts explicitly in Calibration, registers foreground raw relative mouse input on the calibration arena's native window, receives WM_INPUT through that widget's native event handler, grabs Qt mouse events and clips the shared OS pointer to one physical pixel in the arena. The arena's size is held stable for the session so hint/status text changes cannot resize it and interrupt capture; its prior size limits return on cancel, completion or failed start. The final movement in a burst is scheduled for repaint even when it falls within the display-rate throttle. If no movement has arrived after five seconds, the UI shows a non-destructive warning (idleness alone is not treated as a failure); the user can move, or press Esc and Reset to select Desktop pointer. Registration ownership and arena screen position are checked periodically, and a change safely releases capture. Relative counts update a visible in-app cursor at 1 logical pixel per count until its arena edge; clicks are scored from raw button-down, not duplicated Qt clicks. Only the bounded virtual path is passed to the movement analyzer; it is not a full recording of counts beyond the edge. No gain, OS pointer acceleration, DPI compensation or input injection is applied. A null device handle, a second device, absolute input, lost focus/capture/clip ownership, or an unavailable API stops or refuses capture. Device handles are ephemeral session identity checks, not evidence of model, DPI or physical distance. Escape, navigation, moving/resizing the window, hiding and closing release capture; explicit Resume restarts the unfinished target. Previous process raw registration is restored if still owned. If Windows reports a cursor-unclip failure, the warning is retained and further calibration is blocked until restart rather than presenting capture as safe. Other platforms can choose the separately labeled legacy desktop path; raw capture never silently falls back.

No configured-DPI telemetry provider is implemented: Windows Raw Input is not a generic DPI query, and a vendor interface cannot safely be tied to an unknown device without verified model-specific identification. Consequently neither configured DPI changes nor physical sensor drift are detected or compensated. The per-session movement-speed reference and normalized model domain are heuristic baseline statistics, not DPI calibration. Event batching, timing, firmware, display scaling and game processing prevent an end-to-end 1:1 guarantee. Raw Windows capture has fake-API tests but has not been tested on actual Windows hardware.

The AI Coach receives updated summary and recommendation context after **each accepted calibration target**, not each sample. These provisional updates do not themselves trigger a provider request. The context includes current-session rolling scores, sample count, and up to 32 normalized recommendation model points; raw cursor coordinates remain local. Session start clears stale result context; cancellation clears it; completion marks the result final. If the user navigates away while calibration is active, desktop sampling pauses and restarts the unfinished target on return; raw capture releases and requires explicit Resume to restart the target. Review or application of AI proposals is disabled while calibration is in progress. Each chat/proactive request captures the calibration revision; a response arriving after a newer update is discarded instead of affecting the new curve or chat as if current.

Generated recommendations use the native/fallback rolling movement analyzer and its data-derived heuristic; the AI does not receive raw inputs or learn a per-user model. AI numeric sensitivity directives are previewed against the latest measured curve (or its data-informed adjustment of an imported profile), capped to ±10%, and require explicit sending. Generic preset-shape directives are not applied as AI calibration proposals; user-selected manual preset chips remain available and are labeled as such. Prompting and provenance safeguards cannot guarantee that a language model's free-text interpretation is factual; it can still hallucinate. All proposals remain local until explicitly exported or shared in the user's configured AI request.

## Curve compatibility

`curve_io.py` translates between a validated neutral profile and two user-selected formats. Legacy `.ccurve` files use six whitespace-delimited rows (horizontal speeds, multipliers, corner markers, then vertical equivalents). `.cc4` files use JSON and pipe-delimited points. Unknown CC4 fields are retained when an imported profile is re-exported. Generated normalized response points are converted to gain points over a 0–100 input-speed range. Import/export never applies a profile.

## Optional AI chat

`ai_chat.py` uses standard-library HTTP on a background `QThread`. Local mode calls Ollama's `/api/chat` endpoint and defaults to `qwen3:1.7b`; `phi4-mini` is another suggested tag. Online mode calls a user-supplied OpenAI-compatible `/v1/chat/completions` endpoint. Provider URL, model, and temperature can be stored in `QSettings`; API keys remain in memory only.

Text-coaching requests contain the conversation, compact numeric calibration summary, up to 32 normalized recommendation model points, and up to 32 horizontal speed/gain points from the active calibrated or imported curve. Raw movement samples and screen frames are not included in text mode. Live vision is separately opt-in and attaches downscaled preview frames only while Watch live is on. The system prompt keeps the assistant inside coaching, comfort, and manual curve interpretation boundaries and asks for an explicit numeric sensitivity tag when proposing a change; the UI previews it on the current measured profile without applying it.

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


### Dynamic AI performance-curve experiments (rev115)

The shared opt-in `DynamicCurveEngine` is presented behind the reversible Suggested curve card in Recommendation and in Training. Flipping views affects only presentation, not collection or settings. Calibration retains its editable sensitivity graph and contributes telemetry, but has no dynamic panel, graph or view selector. Recommendation and Training use square read-only dynamic charts; Training retains a compact square graph beside the task. Goals are session-only; enabling is off at launch. `PerformanceTelemetry` keeps at most 32 attempts separately per task, with visible-target hit/miss, target-to-click monotonic app-event timing, endpoint error and observed movement speed/path efficiency. In calibration, hits use the visible radius even though the existing analyzer accepts clicks up to 1.65× radius; calibration trial restarts restart the timer; enabling mid-training starts timing at enablement rather than backdating an unobserved interval. The first in-arena re-entry event supplies both local and global origins. Out-of-arena clicks are ignored; training breaks the observed path on exit rather than bridging unobserved movement. Stale delayed target-reveal callbacks are rejected by generation. Training records its scenario and whether the target moves. This is in-app pointer or bounded raw virtual-cursor telemetry, not game crosshair, physical sensor measurement, hardware timestamp or pure reaction time. Only while enabled are the additional records collected. Clearing either history disables the feature and clears its ephemeral observations; existing training analytics are separate.

After three attempts including a hit, a debounced background request sends numeric goals, separate source summaries, recent individual attempts (including observed path length and movement-event count) and sampled horizontal baseline gains to an already-installed, metadata-checked loopback Ollama model. Cloud aliases and remote metadata are rejected, but a loopback daemon can still forward data; disable Ollama cloud features to enforce offline privacy. For an unchanged generated calibration baseline only, a matching calibration input path includes the accepted-trial 95th-percentile in-app speed reference and each available attempt's average path speed divided by that reference, clipped to 1. Mean path speed is not the instantaneous sample speed used to set the reference. Imported/edited/neutral curves, training, and mismatched input paths get no inferred cross-domain mapping. The model supplies 3–16 ordered normalized speed/gain factors; invalid output, bounds beyond ±10%, and missing models are errors, with no AI heuristic fallback. Existing baseline control points are retained and model anchors added within 256-point limits. The ordinary calibration analyzer and its provisional/final graph remain unchanged. One request runs at a time; telemetry changes coalesce to a new request, stale snapshots are labeled and cannot be confirmed, and configuration/base changes invalidate old responses. Inference speed is not guaranteed. A neutral 1× baseline is labeled when training has no measured/imported curve. Only a fresh, manually confirmed proposal changes the in-app Calibration profile, and never during active calibration; exporting remains explicit. Nothing applies to the game. Off/clear drops in-memory telemetry and ignores any eventual in-flight result.

### Curve presentation and manual presets (rev121; supersedes rev116 selector)

Calibration contains only its editable sensitivity graph and retains its existing capture and editing locks. Its Import button uses the down-arrow glyph and Export uses the up-arrow glyph; the underlying file operations are unchanged. As of rev130, the Recommendation card-flip button replaces the former AI Coach disclosure. Activating it reveals the **Dynamic Performance Curve** title, **Enable AI Local Analysis**, goals, status, retry, square preview and explicit confirmation. Training keeps its panel and live monitor; all three dynamic plot areas are square and read-only. Both panels subscribe to one session engine, including Calibration and Training telemetry. Confirming a current model proposal opens Calibration and updates its in-app curve; flipping the card, toggling analysis or inspecting the preview never applies it.

The two persistent icon-above-text buttons in Coach Curve tuning retain their 150×58 geometry and the Dashboard Start button’s painted gold-and-graphite surface, including its inset, border, gradient, glint, focus, hover and disabled treatments. The former Coach disclosure was removed in rev130; the Recommendation button uses the same surface in a compact icon-only square. The preset buttons preview the existing `fast_flicks` and `precision` manual algorithms. They need an imported or measured base and are disabled with a reason otherwise. Their previews use the existing before/after graph and require **Send to calibration** to apply; they do not invoke or stand in for local AI. Duplicate transient chips for those two presets are filtered while other context suggestions remain.

### Coach performance disclosure and telemetry audit (rev119; placement superseded by rev130)

Before rev130, the painted bot-header disclosure was a real checkable keyboard button with an accessible expanded/collapsed and on/off description; the bot eyes also change color while analysis is on. Its panel shared the existing engine, goals, chart, retry and confirmation with Training; Calibration supplies telemetry and receives confirmed curves. It stayed static, including in reduced-motion mode, and the body was collapsed by default. Collection remains opt-in and independent of whether it is open. Discarding insufficient calibration evidence or receiving a model error clears the old proposal graph, preventing an obsolete curve from appearing current. Nothing automatically applies the local model proposal. Tests exercise real in-app desktop events and a simulated Windows Raw transport through movement analysis, click telemetry, source-aware prompt, fake model, preview and explicit confirmation. These tests do not establish real Windows device behavior or verify a real Ollama model.

### CurveCoach window chrome and identity (rev126)

The main window uses a frameless Qt shell with a palette-aware title strip, right-aligned compact accessible minimize/full-screen/close controls, a keyboard window menu, F11 toggle and 6-pixel resize border. A 14-pixel rounded window region is applied in windowed and maximized states, updated on resize and cleared in full screen; platforms that do not support window masks may display square corners. Move/resize request the platform window manager first, falling back to pointer geometry when unsupported. Double-click maximizes/restores; full screen restores the previous normal/maximized state. Full screen and window geometry changes still trigger the existing raw-calibration safety pause. The custom shell cannot guarantee native title-bar snap layouts or all platform window-management features, and actual Windows behavior needs hardware validation. The system-theme selection listens for OS color-scheme changes; explicit light/dark remain fixed.

The refined `brand` painter draws a softly shaded bot over a rising curve chart; the related SVG and multi-resolution Windows ICO live under `assets/branding/`, and the frozen build bundles them. The on-screen name is CurveCoach throughout the interface, but the application and organization names used for QSettings, existing filenames, executable and installer identifiers remain unchanged for compatibility. Audio-mixer guidance acknowledges the legacy executable name.

### Rounded chrome, startup effect and brand typography (rev128)

The custom title strip has an inset 8-pixel rounded border with a small gap above navigation, complementing the 14-pixel window region and keeping all three controls and drag handling intact in windowed, maximized and full-screen states. As before, the Qt platform must support window masks for visible rounded outer corners.

The app entry point invokes a 2.6-second, one-shot `StartupGlow` after showing the window. Four fading, smoothly changing gradient ribbons are drawn in a transparent input-pass-through child of the central widget; the effect neither blocks navigation nor resizes the calibration arena. The shared animations preference and Performance mode suppress it, and disabling animations, hiding or closing stops it. It never uses microphone, audio, external requests, or persistent data.

The navigation wordmark registers bundled Inter variable font from the Google Fonts Inter project (SIL Open Font License, bundled alongside the font). If registration or the file is unavailable, Qt falls back to system sans-serif fonts. The PyInstaller spec and wheel include font and license.

### Rounded navigation (rev129)

The inset navigation frame now uses the same theme-aware 8-pixel radius and fine border as the title strip, with a 3-pixel gap below. Its tabs and scroll viewport are inset vertically so the selected-tab underline and controls stay inside the rounded border. Navigation order, keyboard shortcuts, settings utility, minimum-width layout and full-screen behavior remain unchanged.

### Themed guide chrome and Recommendation flip (rev130)

The Dashboard calibration guide inherits its parent window's live dark/light/system stylesheet. Its frameless resizable shell uses the same 14px window region and an inset rounded title bar with a keyboard-accessible compact close control; close rejects uncommitted answers. Window masks and system move/resize behavior depend on platform support.

The shared performance engine is no longer installed in AI Coach. Recommendation's metallic bot button replaces the right Suggested curve card with a performance panel via a nonblocking horizontal flip; Back reverses it. The stack swaps live faces halfway through a 380ms snapshot animation and switches instantly when animations are disabled or in Performance mode. Switching faces never enables collection or applies a curve. The panel retains the square preview, telemetry, local-analysis opt-in and explicit confirmation, sharing state with Training.

### Recommendation controls (rev131)

The Generate recommendation action is anchored at the bottom of the left recommendation-summary card, including when its dynamic title reads “Add control through the middle of the curve.” Its existing analyzer, active-calibration guard, confirmation flow and Calibration navigation are unchanged. The Dynamic Performance Curve launcher is a 42×42 bot-only button; its accessible name, tooltip, keyboard activation, reversible card flip, shared engine and motion preferences remain intact. The page remains scrollable on shorter windows.

### Numerical curve axes (rev132)

Every `CurveChart`, including its square Recommendation/Training derivatives and the Coach preview, uses shared numbered input-velocity and sensitivity-multiplier axes. Tick labels use up to six significant digits; point hover shows up to twelve. Geometry remains normalized internally for interaction, but explicit axis maxima recover the horizontal profile's stored control-point speed and multiplier. Previous and current profiles use a common display scale; normalized curves from separate runs can correspond to different measured references and should not be treated as physically identical velocities. Changing charts, imports, undo/redo and live cancellation restore the axis metadata. The editable chart reconciles its positions with the actual profile after clamping.

Analyzer recommendations describe normalized output-speed points; `recommendation_profile` converts them to the same velocity/gain points used for export before drawing. The 0.005 point-selection tolerance applies in the pre-conversion output model, not to gain. With an imported base, Recommendation displays the actual adjusted imported profile. Generated x values are normalized 0–100, not measured raw counts/ms or physical velocity. Imported x values are labeled “profile units” because a file alone does not establish the user's Custom Curve normalization configuration. Y is a sensitivity multiplier, not in-game sensitivity. Since rev134 non-Calibration previews show an unweighted CC3-style B-spline approximation (Calibration changed in rev136); Custom Curve smoothing, extrapolation and settings may produce different results. The [Custom Curve user guide](https://mouseacceleration.com/user_guide.html) describes default counts/ms and sensitivity semantics; no equivalent real-time input measurement or claim of exact interpolation is made here.

### Custom Curve 4 graph principles (rev133; removed in rev134, optional approximation restored in rev137)

Rev133 used a weighted-spline preview on all sensitivity chart surfaces (superseded in rev134). It uses a clamped rational B-spline (degree at most three, uniform knots) and the stored 0–1 control-point weights as relative weights with a small positive floor. This is our **approximation**, not the verified Custom Curve 4 algorithm: knot layout, weight mapping, extrapolation/slope modes, driver options and applied sensitivity can differ. Dots show stored control points, which generally do not sit on the curve; their tooltip reports point coordinates and weight, not evaluated driver sensitivity. The former on-graph “CC4-style approx.” labels and accessible descriptions explained the distinction. Imported profiles, before/after overlays and dynamic/Coach previews carry their own weights; CC4 export writes those values. Two points form a straight segment. CC3 files can have different rendering.

In rev133, Calibration allowed right-click weight edits (removed in rev134). A matching vertical point receives the same weight; separately positioned vertical points are left unchanged. At most 64 visible editable control points are shown; typical curves drag only the selected point, with Ctrl restricting motion to one axis. Very long legacy curves retain grouped-handle editing. Because the Custom Curve 4 guide describes a 64-point editor limit, a CC4 export containing more than 64 points on an axis is preserved but displays a compatibility warning; it is not silently truncated. Charts support wheel zoom, dragging blank space to pan and middle-click reset; view changes never modify profiles. The app does not implement CC4's live driver input/sensitivity meters, Shift/Alt whole-curve transforms, endpoint slope handles, separately editable vertical curve or exact interpolation. There is no new claim that normalized recommendation velocity or virtual/desktop speed is raw counts/ms; an exported normalized 0–100 x scale must be reviewed/scaled in Custom Curve before use.

Custom Curve 4 documentation describes control points and adjustable weights: https://mouseacceleration.com/user_guide.html. This implementation was not validated against a running CC4 instance and must not be used as an exact prediction of the driver response.

### Custom Curve 3 graph principles (rev134; Calibration preview superseded in rev136)

The user corrected the reference to Custom Curve **3**. At rev134 all sensitivity previews used the same *unweighted* open, clamped uniform B-spline with up to cubic degree and a dashed control polygon, plus a flat visual extension outside the first/last point. Controls are not necessarily points on the curve. Imported CC4 weights remain stored and exportable but do **not** affect the CC3-style preview; CC4-specific weight editing was removed from the graph. Charts remain zoomable and pannable; normal point dragging changes just the selected control. UI, tooltip and accessibility text identify this as a CC3-style **approximation**, not verified driver behavior. CC3 corner markers and extra device/normalization settings are preserved where supported but are not evaluated in the graph.

A public [CC3-era discussion](https://www.reddit.com/r/MouseAccel/comments/qy94sh/custom_curve_30_driver_released/) describes its B-spline control-point interface. A [public CC3 profile and screenshot](https://whaamkabaam.com/library/6a29e15ab7264177) provided a qualitative visual check of a four-control curve. Another third-party converter describes CC3 as passing through points, but that example's photographed intermediate controls are visibly off the curve; do not rely on that generalization. Neither these observations nor the generic B-spline implementation prove exact Custom Curve 3 knot construction, corner handling, extrapolation or driver output. Native CC3 was not run here.

The export dialog defaults to `.ccurve` and keeps `.cc4` as an option. Imported `.ccurve` files with additional settings rows preserve them on `.ccurve` re-export (six-row files remain six rows); settings are not interpreted. CC4 export retains imported CC4 weights/settings, but the CC3 preview does not represent their effect. Converting CC4 to CC3 loses CC4-only fields and warns; exporting CC3 to CC4 also warns that CC3-only settings and corner behavior may not transfer. No exact automatic cross-version conversion is claimed. Generated x values remain normalized and require review/scaling in the destination application.


### Axis navigation and numeric control-point editing (rev135)

The shared chart separates viewport state from the normalized curve. Wheel over the plot zooms both axes; wheel over a numbered axis strip zooms only that axis. Dragging a blank plot or an axis strip pans the relevant dimensions; middle-click resets. The nonnegative viewport may extend beyond current data (bounded internally), and the approximate visual flat tails extend into it. Curve lines and controls are clipped to the plot. Navigation is available in read-only previews and does not change any curve. Adding by right-click is limited to the current normalized profile rectangle; numeric editing can expand its range. Boundary clicks use a small pixel tolerance.

Calibration's point selector includes every horizontal point, even when large curves display grouped handles. At rev135 a selected point was drawn among the visible handles; rev136 shows all Calibration points. Velocity and multiplier fields show the stored float without the rounded tick-label formatting; Apply/Enter validates finite, nonnegative values, ordering, and the editing limits (velocity up to 1e9; multiplier up to 20). Existing imported values above those limits can be preserved or reduced. Invalid entries produce inline feedback rather than being silently clamped. Applying saves a full undo snapshot and changes only the selected horizontal point and, when both axes were identical, its matching vertical point. Independent vertical data, unrelated points, CC4 weights, corner flags and CC3 extra rows remain intact. A changed endpoint or multiplier rescales the axes and previous overlay together; an active zoom is mapped to the same profile units where possible. Numeric controls and mouse edits are locked during calibration. Read-only proposal/monitor surfaces retain their existing explicit-confirmation behavior. Serialization emits fixed-point decimal strings that round-trip the stored Python floats rather than truncating at ten decimal places; it does not guarantee Custom Curve's own numeric precision or import behavior. The graph is still a labeled approximation, and generated normalized input velocity is not measured counts/ms.


### Calibration plotting alignment (rev136)

Calibration's current and previous lines now use a shape-preserving, piecewise cubic Hermite interpolation through every stored horizontal profile point. Cubic Bézier segment endpoints and dot centers share the identical `_pt` transform. Repeated x values are connected by straight vertical segments, representing the stored coordinates without claiming a unique gain at that speed. Every profile point is drawn as a dot, including large profiles; when many dots overlap, the point selector remains available. Very long curves retain their existing grouped mouse-drag semantics. Generated profile points are derived model anchors, not all raw movement samples.

The shared chart uses the inclusive QRect edges consistently for axis grids, tick placement, hit testing, panning, zoom anchors, dragging, line geometry and point centers. A single `chart_axis_scale` now supplies both overlay normalization and axis metadata, including all-zero and extremely small values; there is no arbitrary tiny-gain floor. Previously an overlay involving a zero-speed profile or sub-1e-9 gain could be normalized against a different denominator from its axis labels, yielding a proportional coordinate error. Current and previous profiles remain on a common scale.

This explicitly supersedes the rev134 B-spline preview **on Calibration only** to satisfy the requested on-line points. The line is a point-interpolating visual aid, not the verified Custom Curve 3 or 4 interpolation and not a simulation of weights, corner flags, settings, extrapolation or driver behavior. The UI labels it “Interpolated · not CC3” and export warns users to review the profile in Custom Curve. At rev136 Recommendation, Coach and Training continued to show their labeled CC3-style B-spline approximation, so their displayed lines could differ from Calibration even for identical stored coordinates. Rev137 adds an optional, synchronized weighted CC4-style preview. No export point coordinates, metadata or confirmation workflows are changed by plotting.


### Switchable CC3/CC4 graph styles (rev137)

Calibration's graph-style selector persists `calibration/graph_style` (`cc3` by default) and synchronizes the shared style to Recommendation, Coach and both Training dynamic-curve previews. Selecting a style is a display preference only: it does not change the profile, points, weights, undo stack, AI telemetry/proposals, or confirmation workflow. Style changes and editing are locked during active Calibration to avoid reflow and provisional edits. The previously requested aligned Calibration view remains the CC3 choice. Other CC3 previews retain their unweighted B-spline approximation.

CC4 selects a rational weighted B-spline approximation with current and previous profiles' own weights and visible control polygons; control dots can sit off the rendered curve. The generic implementation uses clamped uniform knots, degree up to three, 32 samples per span (capped at 4096), and a `1e-4` positive floor for stored zero weights in the visual calculation. It is not verified CC4 driver mathematics: knot vector, point weight interpretation, slope modes, corner markers, extrapolation and device settings may differ. A two-point curve has no visible weight effect in this approximation. No claim of performance improvement for any flick/micro-aim adjustment is made.

With CC4 selected, the Calibration point editor exposes the selected horizontal point's stored `smoothness` as a 0–1 weight. Right-clicking a dot offers “Edit point weight”, focusing the same editor. Apply/Enter validates weight, velocity and multiplier together before taking one undo snapshot. Equal horizontal and vertical axes remain linked; an independently configured vertical curve and all unrelated points/settings remain unchanged. CC3 mode hides the field and preserves stored weights. Existing drag/add/delete and grouped long-curve behavior remain; every Calibration point remains selectable. Weight changes emit the existing profile-changed signal so local AI proposals are invalidated through their established flow. Read-only previews cannot edit or bypass confirmation.

CC4 mode defaults the file dialog to `.cc4`, CC3 mode to `.ccurve`. The `.cc4` encoder round-trips weights and retained CC4 document fields; `.ccurve` cannot store the weights. A confirmation is required before exporting non-neutral weights or a CC4 source to `.ccurve`, and before converting a CC3 source to `.cc4`, which may lose CC3-only settings or corner semantics. The in-app profile is unchanged by export. The actual target app's rendering and format compatibility are not verified.


## Dynamic Magnification (rev138)

`CrosshairOverlay` owns a session-only, 16 ms precise QTimer (a 62.5 Hz target, not a measured or guaranteed frame rate), captures a clipped primary-screen logical-pixel rectangle with `QScreen.grabWindow`, and paints the current pixmap through a circular clip before drawing the crosshair. Fractional source bounds preserve the requested zoom and center; physical-pixel pixmaps are scaled to the logical destination, and clipping preserves the correct placement near edges. The larger even-sized window clamps using the original crosshair size so the crosshair does not shift. Only the current pixmap is kept; disabling, hide and edit clear it and stop the timer. On Windows 10 2004+, `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` must succeed and `GetWindowDisplayAffinity` must confirm it before any grab; disabling restores normal affinity. API acceptance does not prove exclusion on all capture paths. Capture/verification failures are visible.

`ToggleKeyPoller` samples the selected VK as soon as it is armed, then uses a 5 ms precise timer only while the separately session-armed binding is valid, overlay activation is on, and editing is off. It uses the high bit of `GetAsyncKeyState`, waits for an initial release, emits only down edges, and does not consume input. Both timers run on the GUI thread, so slow grabs, paint work or system load can delay key handling and frame updates; Qt coalesces overruns rather than creating a frame queue. The first capture occurs synchronously on activation, and the circular clip path is cached until lens geometry changes. Real-world 60 FPS and instantaneous key response are not guaranteed. Physical mouse buttons are supported; short taps may be missed. Only benign diameter/zoom/binding settings persist; magnification and arming start off. No process-memory access, process injection or input hook is used. See Privacy for capture-path, recording, protected-content and fair-play limitations. Windows, hardware, protected games and anti-cheat were not validated in the Linux build environment.


## Dynamic Magnification error-8 handling and disclosure (rev139)

A Windows `SetWindowDisplayAffinity` error 8 can occur with an already-layered Qt overlay. After that specific failure only, the Win32 adapter reads the original extended style, temporarily removes `WS_EX_LAYERED` from the same HWND, retries the affinity request, restores every original style bit in a `finally` block, then verifies the requested affinity. It repaints the Qt surface. Other errors do not trigger the workaround. If style restoration, retry or verification fails, the attempt fails visibly and the capture timer never starts; a failed style restore asks the user to restart the app. The adapter performs no process injection or privilege escalation. This ordering is a compatibility workaround rather than a guarantee: Windows/DWM, transparency, focus, actual exclusion and capture behavior remain unverified in the Linux test environment. `CrosshairOverlay` records the verified HWND and stops before another screen grab if the native handle changes, avoiding an unexcluded recreated window.

Crosshair uses a collapsed-by-default inline Dynamic Magnification disclosure; opening or closing it does not arm the key or change the capture state. A reported error expands it to show the status. Its control grid shares a label-column width with the crosshair parameter grid. Wrapped notices maintain enough height as their available width changes. See the Privacy document for screen-capture, background-key and fair-play details.


## Raw target retries and point settings (rev140)

The raw native event stream continues across accepted targets until the requested run completes. A hit can be accepted from one real observed segment (start and a delivered movement or observed endpoint), so coalesced single-packet input can progress through all targets; no intermediate samples are invented. That segment's elapsed time includes the time from target spawn to observation, and unseen corrections cannot be resolved. Both native and fallback confidence heuristics scale down when the rolling average is below two observed segments per target, and the UI and recommendation include a sparse-data warning; this scaling is heuristic, not a statistical guarantee. Unusable trials still fail. Previously, a rejected hit reset the analyzer at the hit location but retained that same target, making its start-to-target distance nearly zero and causing repeated rejections. The canvas now chooses a fresh target before restarting an unusable unfinished trial. The target generator uses the actual arena bounds and a farthest-corner fallback if random candidates are too close. Existing foreground-only registration, device-identity, clipping, lost-focus and explicit-resume safety checks are unchanged; the analyzer still uses its eight-trial rolling window. The full 12-target native-dispatch path and coalesced-packet completion and unusable-trial recovery are tested using injected Win32 events, not real Windows hardware.

Calibration's Sensitivity curve has a collapsed-by-default, keyboard-accessible settings button beside the graph-style selector. The in-place panel contains the exact point, velocity, sensitivity, CC4 weight, Apply and local validation controls. Right-click weight editing opens the panel and focuses the field; validation opens it to show the error. Opening/closing does not edit data. It is locked during a calibration run to avoid reflow and to retain the existing active-run editing lock.


## Magnification settings tile (rev141)

Crosshair's collapsed magnification disclosure is a compact scope-icon tile using the same `PresetTile` renderer, fixed dimensions, font and gold surface as the AI Coach preset tiles. Clicking the tile only opens or closes the existing inline settings; it neither toggles the lens nor arms, disarms or consumes the chosen key. Binding selection, separate opt-in arming, activation/position safety and the ability to toggle in the background while the settings are closed are unchanged. A capture error still opens the panel. No screen capture or key monitoring starts merely by opening the panel. These paths are tested with injected key polling and screen/Windows adapters; real Windows hardware and game behavior were not verified here.


## Fitted graph zoom and curve endpoints (rev142)

All `CurveChart` previews use the same navigation policy in CC3 and CC4 styles. Wheel zoom uses 10% per standard notch and proportional fractional angle or touchpad pixel deltas. Plot-area zoom adjusts both axes; scrolling over an axis adjusts only that axis. Horizontal scaling is anchored to the pointer within nonnegative bounds. Vertical scaling is anchored to the current lower edge and cannot shrink past the range needed to show all current and previous control values. If an explicit earlier pan excluded data, the next vertical zoom refits it. The interpolating and positive-weight spline previews are bounded by their controls. Explicit panning remains available and can intentionally crop the curve; middle-click resets the viewport. Selected handles receive enough clipping slack to remain visible at plot edges. Navigation does not alter points, weights, export or the underlying driver curve.

Each rendered path ends at its own final control point. The current-area fill also stops at that x position, rather than painting to the viewport edge. In comparison views the earlier and current curves can end at different positions; neither gets a synthetic horizontal upper-speed tail. The existing pre-first-point visualization is unchanged. CC3/CC4 preview math remains an approximation, not verified driver behavior.


## In-app aim telemetry and playstyle proposals (rev143)

`PerformanceTelemetry` accumulates full-session click error and hit timing plus optional moving-target pointer-event error without retaining tracking coordinates. The AI Coach monitoring switch is separate from the existing dynamic-curve opt-in and starts off at launch. Training collects while either feature is on; only the Coach switch permits a compact aggregate to be attached to a completed `SessionResult`. A switch enabled after a drill starts resets observation and marks the summary partial. Cancelled drills keep the existing basic history behavior but do not write the new aggregate. Legacy history loads with no aggregate. Clear training history disables monitoring, removes saved sessions, and invalidates pending/previewed session proposals.

`analyze_aim_telemetry` computes descriptive accuracy/hit-time differences from explicit goals and normalized click/target error. Moving-target tracking is the percentage and mean error of delivered in-app pointer-move events; it is not time weighted and cannot describe stationary gaps. `recommend_sensitivity_profile` sanitizes the latest 30 recorded training sessions, preserves scenario and target details separately, labels missing data as unknown, and asks verified loopback Ollama to choose bounded numerical factors for the requested playstyle. It has no heuristic fallback, does not imply causality or unique optimality, and never directly changes input. The AI Coach preview uses the existing before/after chart and explicit Send to calibration action. Results are rejected if training history, playstyle, goals, calibration or baseline changed. Monitoring and model requests do not read games, collect screenshots, or persist raw pointer paths.


## Adaptive moving calibration and warm-up (rev144)

Adaptive Calibration is opt-in, off at launch and locked during a run. `AdaptiveDifficulty` is a bounded deterministic controller: a rolling eight in-arena clicks, at least three observations before a change, at most one level per three clicks, radius 10–26 logical pixels and speed 10–120 logical pixels/second. Accuracy uses the visible circle, not the existing wider trial-acceptance tolerance. The timer moves the target inside the active arena and stops or pauses without background monitoring. Arena dimensions are held stable during either input path to avoid reflow when hints change; original sizing limits return on cancellation, completion or a failed raw start. The native and Python analyzers receive the actual target center at click time. Their existing path metrics project the observed cursor path onto the final target direction; the target trajectory is not replayed, and the result is not directly equivalent to static-target calibration. Opt-in ephemeral performance attempts include the click-time radius and, in adaptive mode, target speed, so AI analysis can distinguish changing difficulty. An old native extension without `set_target` leaves the adaptive checkbox disabled rather than silently using stale coordinates. Existing raw input safety, sample thresholds and 12 accepted target count remain.

`generate_personalized_warmup` validates miss/hit totals by scenario from the last 30 saved Training sessions, checks a loopback Ollama model, requests a JSON routine, and accepts only 2–5 known scenarios with 1–6 minutes each and at most 20 minutes total. No misses yields an explicit unavailable result; no model failure is hidden behind a heuristic. The Coach button runs asynchronously, discards results after history/settings changes, and presents a suggestion without starting a drill. No raw input paths or screen frames are sent.


## Privacy and fair-play audit (rev145)

`LiveVisionSession.run(once=True)` collects at most one forced clip and sends at most one image-bearing inference request (model checks or downloads can involve other requests); no frame, encoding errors and provider errors end without a background retry. **Look now** uses that mode when Watch live is off; while already watching it asks the active loop for a look. The one-shot controls stay disabled until the worker exits. Requests already sent cannot be recalled. The filmstrip retains the last displayed clip in memory. Privacy text identifies remote endpoints, possible loopback forwarding, capture continuation, limited process-name query, stored recording and overlay preferences, scratch-file cleanup limitations and model-output uncertainty. Training history clearing reports write failure instead of claiming the disk was cleared; calibration history clearing reports filesystem errors. See `FAIR_PLAY_REVIEW.md` for a bounded source and game-policy assessment; it is not an approval.
