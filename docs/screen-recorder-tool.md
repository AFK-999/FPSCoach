# Screen recorder tool (`tools/screen_recorder.py`)

A standalone developer utility that records the primary monitor to a temporary
video file on a background thread, keeps the newest frame available to the rest
of the process, and runs a separate timer thread that hands that frame to a
callback at a fixed interval.

It exists to exercise the capture/encode side of the project without a GPU, a
Windows build, or the Qt application: codec checks, pacing experiments, and
synthetic frames inside tests.

> **Scope.** The tool lives in `tools/` and is **not imported by
> `fps_coach`**, so the shipped application is unchanged and still uses the
> native D3D11/DXGI path in `native/`. It performs no game analysis and is not
> wired into the UI. The fair-play boundary in `docs/PRIVACY.md` and
> `docs/architecture.md` continues to hold: no gameplay recording, no
> gameplay-derived feedback, no target detection.

## Requirements

```powershell
pip install -e ".[recorder]"      # mss, numpy, opencv-python-headless
```

`mss` and `cv2` are imported defensively, so the module can be imported (and
the tests run) on a machine where one of them is missing. `numpy` is required.

## Command line

```powershell
# record 10 s of the primary monitor, calling the mock analysis every 1.5 s
python tools/screen_recorder.py --duration 10 --analysis-interval 1.5

# pick a monitor (0 = all, 1 = primary, 2+ = secondary), keep the file
python tools/screen_recorder.py --duration 30 --monitor 2 --keep --output-dir .

# diagnostics: show every capture/analysis line
python tools/screen_recorder.py --duration 5 --log-level DEBUG
```

Without `--keep` the temporary video is deleted on exit. The exit path is the
same one that runs when the app is closed: stop event, thread joins, writer
release, file removal.

## Library use

```python
from screen_recorder import ScreenRecorder

def analyse(frame, sequence):
    ...                      # runs on the "screen-analysis" thread

with ScreenRecorder(fps=15, analysis_interval=2.0, analysis_callback=analyse) as rec:
    rec.wait_for_first_frame(5.0)
    frame = rec.latest_frame          # BGR uint8 copy of the newest frame
# both workers joined; temporary video removed
```

Parameters: `fps`, `analysis_interval`, `monitor_index`, `analysis_callback`,
`output_dir`, `fourcc` / `video_suffix` (defaults `mp4v` / `.mp4`),
`keep_temp_file`, `join_timeout`, plus `sct_factory` / `writer_factory` for
tests. Diagnostics: `frames_written`, `analysis_invocations`,
`recorded_seconds`, `capture_error`, `analysis_error`, `video_path`,
`is_running`.

## How the three background pieces work

1. **Frame buffering.** The capture thread (`screen-capture`) writes each frame
   to the video writer and publishes it into `LatestFrameBuffer`, a single-slot
   mailbox behind a lock with a sequence counter. Readers get a private copy;
   a slow reader never adds latency or memory pressure because older frames are
   overwritten.
2. **Analysis timer.** The analysis thread (`screen-analysis`) waits on a
   `threading.Event` until the next absolute deadline, so shutdown is immediate
   and ticks do not drift. Callback exceptions are logged and the timer keeps
   running; ticks that arrive before the first frame are skipped; missed ticks
   after a sleep or a stall are skipped instead of replayed as a burst.
3. **Cleanup.** `__exit__` always calls `cleanup()`, which is idempotent: it
   sets the stop event, joins both workers with `join_timeout`, releases the
   writer and closes the `mss` handle **on the capture thread that created
   them** (`mss` handles are per-thread), clears the buffer, and unlinks the
   temporary file. An `atexit` hook covers an interpreter exit mid-recording.

`stop()` alone keeps the recording; `keep_temp_file=True` keeps it always.
Odd monitor sizes are rounded down to even numbers because `mp4v` rejects odd
frame dimensions.

## Tests

```powershell
python -m pytest tests/test_screen_recorder.py -q      # 32 tests, headless
```

The tests inject a synthetic screen and a fake `cv2.VideoWriter`, so they need
no display and no codec support. They cover buffer integrity under three
concurrent readers, monotonic frame publication, timer cadence, callback
exceptions, cleanup after an exception in the `with` body, idempotent cleanup,
capture/writer failure reporting, and thread/resource release.
