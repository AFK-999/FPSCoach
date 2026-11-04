"""Tests for the screen-recorder engine (``fps_coach.screen_recorder``).

Every test stays headless and codec-free: a synthetic screen and a fake video
writer are injected through ``sct_factory`` / ``writer_factory``.  The Capture
tab wiring is covered separately in ``test_capture_recording.py``.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fps_coach.screen_recorder import (
    LatestFrameBuffer,
    ScreenRecorder,
    dependencies_available,
    missing_dependencies,
    mock_analysis_callback,
)

FAST_FPS = 50.0
TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "screen_recorder.py"


# ---------------------------------------------------------------------------
# Test doubles (shared with test_capture_recording.py)
# ---------------------------------------------------------------------------
from _recording_doubles import Harness  # noqa: E402


@pytest.fixture
def harness() -> Harness:
    return Harness()


def make_recorder(harness: Harness, **kwargs) -> ScreenRecorder:
    params = dict(
        fps=FAST_FPS,
        analysis_interval=0.05,
        monitor_index=1,
        sct_factory=harness.sct_factory,
        writer_factory=harness.writer_factory,
        join_timeout=5.0,
    )
    params.update(kwargs)
    return ScreenRecorder(**params)


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# LatestFrameBuffer
# ---------------------------------------------------------------------------
def test_buffer_is_empty_before_the_first_frame():
    buf = LatestFrameBuffer()
    assert buf.latest is None
    assert buf.sequence == 0
    assert len(buf) == 0
    assert buf.snapshot() == (None, 0)
    assert buf.wait_for_frame(0.01) is False


def test_publish_copies_by_default_and_counts_sequence():
    buf = LatestFrameBuffer()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    assert buf.publish(frame) == 1
    frame[:] = 9  # caller mutates afterwards
    latest, sequence = buf.snapshot()
    assert sequence == 1
    assert np.all(latest == 0)
    assert buf.wait_for_frame(0) is True
    assert len(buf) == 1


def test_snapshot_returns_an_independent_copy():
    buf = LatestFrameBuffer()
    buf.publish(np.full((2, 2, 3), 5, dtype=np.uint8))
    first = buf.latest
    assert first is not None
    first[:] = 200
    assert np.all(buf.latest == 5)
    assert first is not buf.latest


def test_publish_without_copy_keeps_the_array():
    buf = LatestFrameBuffer()
    buf.publish(np.full((2, 2, 3), 7, dtype=np.uint8), copy=False)
    assert np.all(buf.latest == 7)


def test_only_the_latest_frame_is_buffered():
    buf = LatestFrameBuffer()
    for value in range(1, 6):
        buf.publish(np.full((2, 2, 3), value, dtype=np.uint8))
    assert buf.sequence == 5
    assert np.all(buf.latest == 5)
    assert len(buf) == 1


def test_clear_resets_the_buffer():
    buf = LatestFrameBuffer()
    buf.publish(np.zeros((2, 2, 3), dtype=np.uint8))
    buf.clear()
    assert buf.latest is None
    assert buf.sequence == 0
    assert buf.wait_for_frame(0.01) is False


def test_concurrent_readers_never_see_a_torn_frame():
    buf = LatestFrameBuffer()
    stop = threading.Event()
    errors: list[BaseException] = []
    reads = 0

    def writer():
        for index in range(300):
            buf.publish(np.full((8, 8, 3), index % 251, dtype=np.uint8))
            time.sleep(0.001)  # let the readers run
        stop.set()

    def reader():
        nonlocal reads
        try:
            while not stop.is_set():
                frame = buf.latest
                if frame is None:
                    continue
                assert frame.shape == (8, 8, 3)
                if not np.all(frame == frame[0, 0, 0]):
                    raise AssertionError("torn frame observed")
                reads += 1
        except BaseException as exc:  # noqa: BLE001 - surface in the main thread
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(3)]
    producer = threading.Thread(target=writer)
    for thread in readers:
        thread.start()
    producer.start()
    producer.join(10)
    for thread in readers:
        thread.join(10)

    assert errors == []
    assert reads > 0
    assert buf.sequence == 300


# ---------------------------------------------------------------------------
# Capture loop / buffering
# ---------------------------------------------------------------------------
def test_capture_fills_the_buffer_and_the_video(harness):
    recorder = make_recorder(harness)
    with recorder:
        assert recorder.is_running
        assert recorder.wait_for_first_frame(5.0)
        time.sleep(0.25)
        frame = recorder.latest_frame
        sequence = recorder.latest_sequence
        path = recorder.video_path
        assert path is not None and path.exists()  # temp file lives while recording

    # Buffer: BGR uint8, primary monitor, even dimensions.
    assert frame is not None
    assert frame.shape == (480, 640, 3)
    assert frame.dtype == np.uint8
    assert sequence > 0
    # cleanup() clears the buffer on purpose.
    assert recorder.latest_frame is None
    assert recorder.latest_sequence == 0

    # Video side: every captured frame reached the writer.
    writer = harness.writer
    assert len(writer.frames) == recorder.frames_written > 1
    assert writer.frames[0].shape == (480, 640, 3)
    assert writer.size == (640, 480)
    assert writer.write_thread.name == "screen-capture"

    # Resource safety: released and closed on the capture thread itself.
    assert writer.released
    assert writer.release_thread.name == "screen-capture"
    assert harness.screen.closed
    assert not recorder.is_running
    assert not path.exists()


def test_frames_are_published_in_monotonic_order(harness):
    recorder = make_recorder(harness, fps=100.0)
    observed: list[int] = []
    with recorder:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(observed) < 5:
            if recorder.latest_frame is not None:
                sequence = recorder.latest_sequence
                if not observed or sequence > observed[-1]:
                    observed.append(sequence)
            time.sleep(0.005)
    assert len(observed) >= 5
    assert observed == sorted(observed)
    assert len(set(observed)) == len(observed)


def test_both_workers_exist_as_daemon_threads(harness):
    recorder = make_recorder(harness)
    recorder.start()
    try:
        threads = {thread.name: thread for thread in threading.enumerate()}
        assert threads["screen-capture"].daemon
        assert threads["screen-analysis"].daemon
    finally:
        recorder.cleanup()


def test_odd_monitor_dimensions_are_rounded_down(harness):
    recorder = make_recorder(harness, monitor_index=0)  # 641x481 monitor
    with recorder:
        assert recorder.wait_for_first_frame(5.0)
        frame = recorder.latest_frame
    assert frame is not None and frame.shape == (480, 640, 3)


# ---------------------------------------------------------------------------
# Concurrent analysis timer
# ---------------------------------------------------------------------------
def test_analysis_callback_fires_repeatedly_on_schedule(harness):
    calls: list[tuple[int, np.ndarray, str, float]] = []
    lock = threading.Lock()

    def callback(frame, sequence):
        with lock:
            calls.append((sequence, frame, threading.current_thread().name, time.monotonic()))

    recorder = make_recorder(harness, analysis_interval=0.1, analysis_callback=callback)
    with recorder:
        time.sleep(0.65)  # allow ~6 ticks

    snapshot = list(calls)
    assert len(snapshot) >= 3
    assert len(snapshot) == recorder.analysis_invocations
    for sequence, frame, thread_name, _ in snapshot:
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (480, 640, 3)
        assert sequence > 0
        assert thread_name == "screen-analysis"
    gaps = [
        later[3] - earlier[3]
        for earlier, later in zip(snapshot, snapshot[1:], strict=False)
    ]
    assert all(gap > 0.04 for gap in gaps), gaps
    assert max(gaps) < 0.5, gaps


def test_analysis_interval_is_respected(harness):
    calls: list[float] = []
    recorder = make_recorder(
        harness,
        analysis_interval=0.5,
        analysis_callback=lambda frame, sequence: calls.append(time.monotonic()),
    )
    started = time.monotonic()
    with recorder:
        time.sleep(1.15)
    # About two ticks in 1.15 s at 0.5 s: no catch-up burst.
    assert 2 <= len(calls) <= 3
    assert calls[0] - started >= 0.35


def test_callback_gets_a_private_copy_of_the_frame(harness):
    checks: list[bool] = []

    def inspector(frame, sequence):
        other = recorder.latest_frame
        checks.append(other is not None and not np.shares_memory(other, frame))
        frame[:] = 255  # a hostile consumer must not corrupt shared state

    recorder = make_recorder(harness, analysis_callback=inspector)
    with recorder:
        assert wait_until(lambda: bool(checks), 5.0)
        time.sleep(0.1)
        assert not np.all(recorder.latest_frame == 255)
    assert all(checks)


def test_callback_exception_does_not_stop_the_timer_or_recording(harness, caplog):
    attempts: list[int] = []

    def flaky(frame, sequence):
        attempts.append(sequence)
        raise ValueError("mock analysis exploded")

    recorder = make_recorder(harness, analysis_interval=0.05, analysis_callback=flaky)
    with caplog.at_level("ERROR", logger="fps_coach.screen_recorder"):
        with recorder:
            time.sleep(0.4)

    assert len(attempts) >= 2, "timer died after the first error"
    assert recorder.frames_written > 1, "recording was affected"
    assert recorder.capture_error is None
    assert recorder.video_path is not None
    assert not recorder.video_path.exists()
    assert any("analysis callback raised" in record.message for record in caplog.records)


def test_timer_waits_for_the_first_frame(harness):
    harness.mss_kwargs["grab_delay"] = 0.12  # several ticks before any frame exists
    payloads: list[np.ndarray] = []
    recorder = make_recorder(
        harness,
        analysis_interval=0.02,
        analysis_callback=lambda frame, sequence: payloads.append(frame),
    )
    with recorder:
        time.sleep(0.3)
    assert payloads
    assert all(payload is not None for payload in payloads)
    assert len(payloads) == recorder.analysis_invocations


def test_stop_from_inside_the_callback_does_not_deadlock(harness):
    fired = threading.Event()

    def stopper(frame, sequence):
        fired.set()
        recorder.stop()  # called on the analysis thread itself

    recorder = make_recorder(harness, analysis_interval=0.05, analysis_callback=stopper)
    recorder.start()
    assert fired.wait(5.0)
    recorder.stop()  # second call, from the main thread
    assert not recorder.is_running
    recorder.cleanup()
    names = {thread.name for thread in threading.enumerate()}
    assert "screen-analysis" not in names
    assert "screen-capture" not in names


# ---------------------------------------------------------------------------
# Cleanup / resource safety
# ---------------------------------------------------------------------------
def test_cleanup_is_idempotent_and_removes_the_temp_file(harness):
    recorder = make_recorder(harness)
    with recorder:
        assert recorder.wait_for_first_frame(5.0)
        path = recorder.video_path
        assert path is not None and path.exists()
    assert not path.exists()

    recorder.cleanup()  # second call is a no-op
    assert not path.exists()
    assert harness.writer.release_calls == 1
    assert harness.screen.close_calls == 1


def test_cleanup_runs_when_the_with_body_raises(harness):
    recorder = make_recorder(harness)
    path = None
    with pytest.raises(RuntimeError, match="boom"):
        with recorder:
            assert recorder.wait_for_first_frame(5.0)
            path = recorder.video_path
            assert path is not None and path.exists()
            raise RuntimeError("boom")
    assert not path.exists()
    assert harness.writer.released
    assert not recorder.is_running
    assert recorder.latest_frame is None


def test_keep_temp_file_and_output_dir(harness, tmp_path):
    recorder = make_recorder(harness, output_dir=tmp_path, keep_temp_file=True)
    with recorder:
        assert recorder.wait_for_first_frame(5.0)
        path = recorder.video_path
    assert path is not None
    assert path.exists()
    assert path.parent == tmp_path.resolve()
    assert path.name.startswith("bg_capture_")
    assert path.name.endswith(".mp4")
    assert path.stat().st_size > 0
    assert len(harness.writer.frames) == recorder.frames_written


def test_stop_is_idempotent_and_joins_threads(harness):
    recorder = make_recorder(harness)
    recorder.start()
    assert recorder.wait_for_first_frame(5.0)
    recorder.stop()
    recorder.stop()
    names = {thread.name for thread in threading.enumerate()}
    assert "screen-capture" not in names
    assert "screen-analysis" not in names
    assert not recorder.is_running
    # stop() keeps the file; cleanup() deletes it.
    path = recorder.video_path
    assert path is not None and path.exists()
    recorder.cleanup()
    assert not path.exists()


def test_capture_error_is_reported_and_the_file_removed(harness, caplog):
    harness.mss_kwargs["fail_after"] = 2
    recorder = make_recorder(harness, fps=100.0)
    with caplog.at_level("ERROR", logger="fps_coach.screen_recorder"):
        with recorder:
            assert wait_until(lambda: recorder.capture_error is not None, 5.0)
    assert isinstance(recorder.capture_error, OSError)
    assert recorder.video_path is not None
    assert not recorder.video_path.exists()
    assert harness.writer.released
    assert harness.screen.closed
    assert recorder.analysis_error is None


def test_writer_open_failure_reports_an_actionable_error():
    harness = Harness(opened=False)
    recorder = make_recorder(harness)
    with recorder:
        assert wait_until(lambda: recorder.capture_error is not None, 5.0)
    assert isinstance(recorder.capture_error, RuntimeError)
    assert "VideoWriter" in str(recorder.capture_error)
    assert not recorder.video_path.exists()


def test_atexit_hook_cleans_up(harness):
    recorder = make_recorder(harness)
    recorder.start()
    assert recorder.wait_for_first_frame(5.0)
    path = recorder.video_path
    recorder._atexit_cleanup()  # simulated interpreter shutdown hook
    assert path is not None and not path.exists()
    assert not recorder.is_running


def test_double_start_and_restart_after_cleanup_raise(harness):
    recorder = make_recorder(harness)
    recorder.start()
    with pytest.raises(RuntimeError):
        recorder.start()
    recorder.cleanup()
    with pytest.raises(RuntimeError):
        recorder.start()


# ---------------------------------------------------------------------------
# Packaging: the CLI front end and the optional-dependency report
# ---------------------------------------------------------------------------
def test_dependency_report_matches_the_imports():
    missing = missing_dependencies()
    assert isinstance(missing, list)
    assert dependencies_available() is (not missing)


def test_cli_front_end_runs_without_a_display():
    result = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "--analysis-interval" in result.stdout


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        {"fps": 0},
        {"analysis_interval": 0},
        {"join_timeout": -1},
        {"video_suffix": "mp4"},
    ],
)
def test_invalid_parameters_raise(harness, kwargs):
    with pytest.raises(ValueError):
        make_recorder(harness, **kwargs)


def test_default_callback_is_the_mock_analysis(harness):
    assert make_recorder(harness).analysis_callback is mock_analysis_callback


def test_mock_analysis_callback_reads_the_frame(caplog):
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    with caplog.at_level("INFO", logger="fps_coach.screen_recorder"):
        mock_analysis_callback(frame, 7)
    assert "sequence=7" in caplog.text


def test_monitor_index_out_of_range_is_reported(harness):
    recorder = make_recorder(harness, monitor_index=9)
    with recorder:
        assert wait_until(lambda: recorder.capture_error is not None, 5.0)
    assert isinstance(recorder.capture_error, IndexError)
