"""Capture tab: the Recording card, its service, and the scratch-file lifecycle.

Offscreen Qt, synthetic screen, fake encoder: no display and no codec needed.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from _recording_doubles import Harness  # noqa: E402
from fps_coach.capture_recording import (  # noqa: E402
    AnalysisTick,
    RecordingService,
    RecordingStatus,
    RecordingSummary,
)
from fps_coach.screen_recorder import ScreenRecorder  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved(monkeypatch, tmp_path):
    from fps_coach.ui import pages_setup
    from PySide6.QtCore import QSettings

    store = QSettings(str(tmp_path / "capture.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_setup, "_settings", lambda: store)
    return store


class _FakeBackend:
    """Native-backend stub: the native capture path is never exercised here."""

    native_available = True

    def outputs(self):
        return [{
            "output_name": "Display 1", "adapter_name": "GPU",
            "width": 1920, "height": 1080, "adapter_index": 0, "output_index": 0,
        }]

    def capture_stats(self):
        return {}

    def latest_frame(self):
        return None

    def start_capture(self, *args):
        pass

    def stop_capture(self):
        pass

    def close(self):
        pass


@pytest.fixture
def make_page(qapp, saved, monkeypatch):
    """Build a CapturePage whose RecordingService uses the fake screen/writer."""
    from fps_coach.ui import pages_setup

    created = []

    def _factory(harness: Harness):
        class _InjectedService(RecordingService):
            def __init__(self, parent=None):
                super().__init__(
                    parent,
                    recorder_factory=lambda **kwargs: ScreenRecorder(
                        sct_factory=harness.sct_factory,
                        writer_factory=harness.writer_factory,
                        **kwargs,
                    ),
                )

        monkeypatch.setattr(pages_setup, "RecordingService", _InjectedService)
        page = pages_setup.CapturePage(_FakeBackend())
        created.append(page)
        return page

    yield _factory

    for page in created:
        page.shutdown()
        page.deleteLater()
    qapp.processEvents()


def wait_for_error(service: RecordingService, timeout: float = 8.0) -> RecordingStatus:
    """Poll the service until the recorder reports a failure."""
    status = service.poll()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not status.error:
        time.sleep(0.01)
        status = service.poll()
    return status


def pump(qapp, predicate, timeout: float = 5.0, page=None) -> bool:
    """Process Qt events (timers included) until ``predicate`` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if page is not None:
            page._refresh_stats()  # the page's own 250 ms timer, driven by hand
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


# ---------------------------------------------------------------------------
# The card itself
# ---------------------------------------------------------------------------
def test_capture_tab_has_an_idle_recording_card(make_page):
    page = make_page(Harness())
    assert page.record_start_btn.isEnabled()
    assert not page.record_stop_btn.isEnabled()
    assert page.record_status.text() == "Idle"
    assert [page.record_fps.itemData(i) for i in range(page.record_fps.count())] == [15, 30, 60]
    assert page.record_fps.currentData() == 30
    assert page.record_interval.value() == pytest.approx(2.0)
    assert page.record_interval.suffix() == " s"
    assert "primary display" in page.record_note.text()


def test_recording_settings_persist(make_page, saved):
    page = make_page(Harness())
    index = page.record_fps.findData(60)
    page.record_fps.setCurrentIndex(index)
    page.record_interval.setValue(1.5)
    assert int(saved.value("capture/record_fps")) == 60
    assert float(saved.value("capture/record_interval")) == pytest.approx(1.5)


def test_missing_dependencies_disable_the_card(qapp, saved, monkeypatch):
    from fps_coach import screen_recorder
    from fps_coach.ui import pages_setup

    monkeypatch.setattr(
        screen_recorder, "missing_dependencies", lambda: ["mss", "opencv-python-headless"]
    )
    page = pages_setup.CapturePage(_FakeBackend())
    try:
        assert not page.record_start_btn.isEnabled()
        assert not page.record_interval.isEnabled()
        assert page.record_status.text() == "Unavailable"
        assert 'pip install -e ".[recorder]"' in page.record_detail.text()
        assert "Install the recorder extra" in page.record_note.text()
    finally:
        page.shutdown()
        page.deleteLater()


# ---------------------------------------------------------------------------
# Start / stop through the page
# ---------------------------------------------------------------------------
def test_record_writes_a_scratch_file_and_stop_deletes_it(make_page, qapp):
    harness = Harness()
    page = make_page(harness)
    page._start_recording()
    assert page.recording.is_recording

    assert pump(qapp, lambda: harness.writer.frames, page=page), "no frames were written"
    path = page.recording.poll().path
    assert path and Path(path).exists()
    assert pump(qapp, lambda: page.record_status.text().startswith("Recording"), page=page)
    assert not page.record_start_btn.isEnabled()
    assert page.record_stop_btn.isEnabled()

    page._stop_recording()
    qapp.processEvents()
    assert not page.recording.is_recording
    assert not Path(path).exists(), "scratch file must be deleted"
    assert page.record_status.text().startswith("Stopped")
    assert page.record_start_btn.isEnabled()
    assert not page.record_stop_btn.isEnabled()
    assert "discarded" in page.record_detail.text()
    # The log line reports the real counters, not placeholders.
    assert "frames" in page.record_status.text()


def test_analysis_ticks_reach_the_card(make_page, qapp):
    harness = Harness()
    page = make_page(harness)
    page.record_interval.setValue(0.5)
    page._start_recording()
    try:
        assert pump(
            qapp,
            lambda: "Analysis #" in page.record_analysis.text(),
            timeout=8.0,
            page=page,
        ), f"no analysis tick; label={page.record_analysis.text()!r}"
        text = page.record_analysis.text()
        assert "frame" in text and "mean" in text
        assert page.recording.analysis_count >= 1
    finally:
        page._stop_recording()
        qapp.processEvents()


def test_keep_is_off_by_default_and_persists_when_toggled(make_page, saved):
    page = make_page(Harness())
    assert not page.record_keep.isChecked()
    assert not page.record_dir_edit.isEnabled()  # folder row only matters when keeping
    page.record_keep.setChecked(True)
    assert bool(saved.value("capture/record_keep")) is True
    assert page.record_dir_edit.isEnabled()
    assert page.record_dir_btn.isEnabled()
    page.record_keep.setChecked(False)
    assert bool(saved.value("capture/record_keep")) is False


def test_default_record_dir_is_under_movies(make_page):
    page = make_page(Harness())
    assert Path(page.record_dir_edit.text()).name == "FPSCoach"


def test_kept_recording_is_saved_to_the_chosen_folder(make_page, qapp, tmp_path):
    harness = Harness()
    page = make_page(harness)
    page.record_keep.setChecked(True)
    page.record_dir_edit.setText(str(tmp_path / "recordings"))
    page._start_recording()
    assert pump(qapp, lambda: harness.writer.frames, page=page)

    page._stop_recording()
    qapp.processEvents()

    assert page.record_status.text().startswith("Saved")
    assert "Recording saved to" in page.record_detail.text()
    saved = list((tmp_path / "recordings").glob("fpscoach-recording-*.mp4"))
    assert len(saved) == 1, "the kept recording was not written to the chosen folder"
    assert saved[0].stat().st_size > 0
    assert str(saved[0]) in page.record_detail.text()
    assert page.record_start_btn.isEnabled()


def test_kept_recording_survives_closing_the_page(make_page, qapp, tmp_path):
    harness = Harness()
    page = make_page(harness)
    page.record_keep.setChecked(True)
    page.record_dir_edit.setText(str(tmp_path))
    page._start_recording()
    assert pump(qapp, lambda: harness.writer.frames, page=page)

    page.shutdown()  # what MainWindow.closeEvent does
    qapp.processEvents()
    assert not page.recording.is_recording
    saved = list(Path(tmp_path).glob("fpscoach-recording-*.mp4"))
    assert len(saved) == 1
    assert saved[0].stat().st_size > 0


def test_analysis_interval_range_was_widened(make_page):
    page = make_page(Harness())
    assert page.record_interval.minimum() == pytest.approx(0.5)
    assert page.record_interval.maximum() == pytest.approx(60.0)  # was 30 s


def test_shutdown_while_recording_deletes_the_file(make_page, qapp):
    harness = Harness()
    page = make_page(harness)
    page._start_recording()
    assert pump(qapp, lambda: harness.writer.frames, page=page)
    path = page.recording.poll().path
    assert path and Path(path).exists()

    page.shutdown()
    qapp.processEvents()
    assert not page.recording.is_recording
    assert not Path(path).exists()
    assert not page.record_start_btn.isEnabled()


def test_scratch_file_survives_a_failed_analysis_and_failing_capture(make_page, qapp):
    harness = Harness(fail_after=2)  # the fake display disconnects after two grabs
    page = make_page(harness)
    page._start_recording()
    assert pump(qapp, lambda: page.record_status.text() == "Recording stopped", timeout=8.0, page=page)
    assert "simulated display disconnect" in page.record_detail.text()
    assert not page.recording.is_recording
    assert page.record_start_btn.isEnabled()
    if harness.writers:
        assert harness.writers[-1].released
        assert not harness.writers[-1].path.exists()


# ---------------------------------------------------------------------------
# RecordingService on its own
# ---------------------------------------------------------------------------
def _service(qapp, harness: Harness) -> RecordingService:
    return RecordingService(
        recorder_factory=lambda **kwargs: ScreenRecorder(
            sct_factory=harness.sct_factory, writer_factory=harness.writer_factory, **kwargs
        )
    )


def test_service_emits_started_analysis_and_stopped(qapp):
    harness = Harness()
    service = _service(qapp, harness)
    events: list[tuple] = []
    service.started.connect(lambda path: events.append(("started", path)))
    service.analysis.connect(lambda tick: events.append(("analysis", tick)))
    service.stopped.connect(lambda summary: events.append(("stopped", summary)))

    assert service.start(fps=60.0, analysis_interval=0.1, monitor_index=1)
    assert service.is_recording
    assert pump(qapp, lambda: any(kind == "analysis" for kind, *_ in events), timeout=8.0)

    summary = service.stop()
    qapp.processEvents()
    kinds = [kind for kind, *_ in events]
    assert "started" in kinds
    assert "analysis" in kinds
    assert kinds[-1] == "stopped"
    assert isinstance(summary, RecordingSummary)
    # A frame can land between reading the counters and releasing the writer.
    assert 0 < summary.frames <= len(harness.writers[-1].frames)
    assert summary.frames >= len(harness.writers[-1].frames) - 1
    assert summary.analyses >= 1
    tick = next(payload for kind, payload in events if kind == "analysis")
    assert isinstance(tick, AnalysisTick)
    assert tick.width == 640 and tick.height == 480
    assert 0.0 <= tick.mean <= 255.0
    assert not service.is_recording
    assert not harness.writers[-1].path.exists()


def test_service_keeps_the_file_when_asked(qapp, tmp_path):
    harness = Harness()
    service = _service(qapp, harness)
    assert service.start(fps=60.0, analysis_interval=5.0, keep=True, save_dir=str(tmp_path / "out"))
    assert pump(qapp, lambda: harness.writers and harness.writers[-1].frames, timeout=8.0)

    summary = service.stop()
    assert summary is not None and summary.kept is True
    saved = Path(summary.path)
    assert saved.exists()
    assert saved.parent == (tmp_path / "out").resolve()
    assert saved.name.startswith("fpscoach-recording-")
    assert not list(saved.parent.glob("bg_capture_*")), "the scratch name must be replaced"


def test_service_discards_the_file_by_default(qapp):
    harness = Harness()
    service = _service(qapp, harness)
    assert service.start(fps=60.0, analysis_interval=5.0)
    assert pump(qapp, lambda: harness.writers and harness.writers[-1].frames, timeout=8.0)
    summary = service.stop()
    assert summary is not None and summary.kept is False
    assert not Path(summary.path).exists()


def test_service_shutdown_keeps_a_requested_file(qapp, tmp_path):
    harness = Harness()
    service = _service(qapp, harness)
    assert service.start(fps=60.0, analysis_interval=5.0, keep=True, save_dir=str(tmp_path))
    assert pump(qapp, lambda: harness.writers and harness.writers[-1].frames, timeout=8.0)
    service.shutdown()
    saved = list(Path(tmp_path).glob("fpscoach-recording-*.mp4"))
    assert len(saved) == 1


def test_service_poll_reports_capture_errors_and_stops(qapp):
    harness = Harness(fail_after=1)
    service = _service(qapp, harness)
    failures: list[str] = []
    service.failed.connect(failures.append)
    assert service.start(fps=100.0, analysis_interval=5.0)

    status = wait_for_error(service)
    assert "simulated display disconnect" in status.error
    assert failures and "simulated display disconnect" in failures[-1]
    assert not service.is_recording
    assert harness.writers[-1].released
    assert not harness.writers[-1].path.exists()


def test_service_start_twice_is_idempotent_and_stop_is_safe(qapp):
    harness = Harness()
    service = _service(qapp, harness)
    assert service.start(fps=60.0, analysis_interval=5.0)
    assert service.start(fps=60.0, analysis_interval=5.0)  # already running
    assert len(harness.writers) == 1
    assert service.stop() is not None
    assert service.stop() is None  # nothing to stop
    service.shutdown()  # idempotent after a normal stop
    assert not service.is_recording


def test_service_shutdown_is_quiet(qapp):
    harness = Harness()
    service = _service(qapp, harness)
    stopped: list[object] = []
    service.stopped.connect(stopped.append)
    assert service.start(fps=60.0, analysis_interval=5.0)
    service.shutdown()
    qapp.processEvents()
    assert not service.is_recording
    assert stopped == [], "shutdown must not emit stopped()"
    assert not harness.writers[-1].path.exists()
    assert service.start(fps=60.0, analysis_interval=5.0) is False  # closed for good


# ---------------------------------------------------------------------------
# Window close
# ---------------------------------------------------------------------------
def test_main_window_close_stops_the_recording(qapp):
    from fps_coach.backend import CoreBackend
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow(CoreBackend())
    calls: list[int] = []
    window.capture_page.shutdown = lambda: calls.append(1)
    window.close()
    assert calls == [1], "MainWindow.closeEvent must stop the scratch recording"
