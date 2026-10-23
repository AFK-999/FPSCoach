# Screen recorder (Capture tab)

The Capture tab has a **Recording** card that records the primary display in the
background while an analysis timer samples the newest frame at a fixed interval.
By default the file is deleted when you stop or close the app; tick **Keep the
recording** to save it to a folder you choose instead.

```
Capture tab page  (ui/pages_setup.py, class CapturePage)
        │  Record / Stop, rate + interval combos, status labels
        ▼
RecordingService  (capture_recording.py)      Qt signals: started, analysis, stopped, failed
        │  start / stop / poll / shutdown
        ▼
ScreenRecorder    (screen_recorder.py)        no Qt dependency
        ├── screen-capture thread   mss.grab → cv2.VideoWriter → LatestFrameBuffer
        └── screen-analysis thread  analysis_callback(frame_copy, sequence) every N s
```

`tools/screen_recorder.py` is a command-line front end for the same engine, for
codec checks and timing experiments outside the app.

## What the card does

- **Recording rate** (15 / 30 / 60 FPS) and **Analysis every** (0.5 – 60 s) are
  saved in `QSettings` as `capture/record_fps` and `capture/record_interval`.
- **Keep the recording** and the recordings folder are saved as
  `capture/record_keep` and `capture/record_dir` (default `Videos/FPSCoach`).
- **Record** writes an `.mp4` (fourcc `mp4v`) to the system temp folder, or to
  the chosen folder when keeping; the status line ticks up with elapsed time,
  frames and analyses.
- **Stop** joins both worker threads, releases the encoder, closes the `mss`
  handle on the capture thread that created it, and then either deletes the
  file (default) or renames it to `fpscoach-recording-<date>-<time>.mp4` in the
  chosen folder. The status reports frames, seconds and the analysis calls.
- The analysis line shows `#count · frame sequence · W×H · mean brightness` — a
  pipeline health check, not a gameplay analysis.
- If `mss`, `numpy` or `opencv-python-headless` is missing, the card disables
  itself and tells you to install the `recorder` extra.
- `MainWindow.closeEvent` calls `CapturePage.shutdown()`, so closing the app
  mid-recording still tidies up: the file is deleted unless **Keep** is ticked,
  in which case it is saved like a normal Stop.

The card records the **primary display** through `mss`, independently of the
native D3D11/DXGI preview above it. It is not tied to the game window selection:
a window that covers the display appears in the recording exactly as it appears
on screen.

## Install

```powershell
pip install -e ".[dev,recorder]"     # mss, numpy, opencv-python-headless
```

The app **runs without** these packages: the Recording card shows *Unavailable* with the
install command instead of disabling itself silently. If you see
`AttributeError: 'NoneType' object has no attribute 'ndarray'` at start-up, that was a
rev73-rev75 bug (the module evaluated a `numpy` type alias at import time); rev76 fixes it,
and installing the extra above enables the feature either way.

`scripts/bootstrap.ps1` and `scripts/build.ps1` already install the `recorder`
extra, and `installer/fpscoach.spec` collects `mss` submodules because that
package chooses its platform backend at run time.

## Engine behaviour

Three properties hold in `ScreenRecorder`:

1. **Latest-frame buffering.** The capture thread publishes each frame into
   `LatestFrameBuffer`: one slot behind a lock, a sequence counter, and a
   private copy per read. A slow consumer never adds latency or memory growth,
   and no reader can observe a torn frame.
2. **Concurrent timer.** The analysis thread waits on a `threading.Event` until
   an absolute deadline, so shutdown is instant and ticks do not drift. Ticks
   before the first frame are skipped, missed ticks after a sleep are skipped
   rather than replayed, and a callback that raises is logged without stopping
   the timer or the recording.
3. **Cleanup.** `stop()` → bounded joins → writer release and `mss` close on the
   capture thread → buffer clear → file unlink. `cleanup()` is idempotent,
   `__exit__` always runs it, and an `atexit` hook covers an interpreter exit
   mid-recording.

Odd display sizes are rounded down to even numbers (`mp4v` rejects odd
dimensions). `monitor_index=1` is the primary monitor; `0` would be all
monitors, `2+` secondary ones.

## Tests

```powershell
python -m pytest tests/test_screen_recorder.py tests/test_capture_recording.py -q
```

`tests/test_screen_recorder.py` (34 tests) covers the engine: buffer integrity
under three concurrent readers, monotonic sequences, timer cadence, callback
isolation, cleanup after exceptions, capture/writer failure reporting, and the
CLI front end. `tests/test_capture_recording.py` (12 tests) covers the Qt layer
and the card offscreen: settings persistence, disabled state without the extra,
start/stop through the page, analysis ticks reaching the labels, `shutdown()`
while recording, and `MainWindow.closeEvent`. `tests/_recording_doubles.py`
holds the synthetic screen and fake encoder both suites share.

## Scope

Recording is user-controlled. It starts when you press Record and stops when you
press Stop or close the app; the file is discarded at Stop unless **Keep the
recording** is ticked, in which case it stays in the folder you chose until you
delete it. Nothing is uploaded by the app, and the recording is not analysed for
targets, aim, or gameplay events (the timer samples frame statistics only).
`docs/PRIVACY.md` and `docs/architecture.md` document the same boundary.
