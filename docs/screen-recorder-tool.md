# Screen recorder (Capture tab)

The Capture tab has a **Recording** card that records the primary display to a
scratch video in the background while an analysis timer samples the newest frame
at a fixed interval. The file is deleted when recording stops or the app closes.

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

- **Recording rate** (15 / 30 / 60 FPS) and **Analysis every** (0.5 – 30 s) are
  saved in `QSettings` as `capture/record_fps` and `capture/record_interval`.
- **Record** writes to a temporary `.mp4` (fourcc `mp4v`) in the system temp
  folder; the status line ticks up with elapsed time, frames and analyses.
- **Stop** joins both worker threads, releases the encoder, closes the `mss`
  handle on the capture thread that created it, and deletes the file. The
  status then reports frames, seconds and how many analysis calls ran.
- The analysis line shows `#count · frame sequence · W×H · mean brightness` — a
  pipeline health check, not a gameplay analysis.
- If `mss`, `numpy` or `opencv-python-headless` is missing, the card disables
  itself and tells you to install the `recorder` extra.
- `MainWindow.closeEvent` calls `CapturePage.shutdown()`, so closing the app
  mid-recording still deletes the scratch file.

The card records the **primary display** through `mss`, independently of the
native D3D11/DXGI preview above it. It is not tied to the game window selection:
a window that covers the display appears in the recording exactly as it appears
on screen.

## Install

```powershell
pip install -e ".[dev,recorder]"     # mss, numpy, opencv-python-headless
```

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

Recording is user-started and short-lived: it stops with the app and the file is
deleted on stop. It is never uploaded, and it is not analysed for targets, aim,
or gameplay events. `docs/PRIVACY.md` and `docs/architecture.md` document the
same boundary.
