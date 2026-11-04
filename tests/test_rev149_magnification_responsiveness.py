"""Magnifier scheduling and lifecycle tests with synthetic screen/key adapters.

These tests do not measure FPS or latency on Windows or in any game.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fps_coach.crosshair import CrosshairConfig
from fps_coach.ui import crosshair_overlay, magnification
from fps_coach.ui.crosshair_overlay import CrosshairOverlay
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Screen:
    def __init__(self):
        self.count = 0

    def geometry(self):
        return QRect(0, 0, 800, 600)

    def availableGeometry(self):
        return self.geometry()

    def grabWindow(self, _window, _x, _y, width, height):
        self.count += 1
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor(self.count % 256, 20, 30))
        return pixmap


@pytest.fixture
def setup_screen(app, monkeypatch):
    screen = Screen()
    calls = []
    monkeypatch.setattr(crosshair_overlay.QApplication, "primaryScreen", lambda: screen)
    monkeypatch.setattr(crosshair_overlay, "set_capture_exclusion",
                        lambda hwnd, enabled: (calls.append((hwnd, enabled)) or (True, "")))
    return screen, calls


def test_precise_timer_and_first_frame_on_activation(app, setup_screen):
    screen, affinity = setup_screen
    overlay = CrosshairOverlay(CrosshairConfig(), centre=QPoint(400, 300))
    overlay.show()
    try:
        assert overlay._magnification_timer.interval() == 16
        assert overlay._magnification_timer.timerType() == Qt.TimerType.PreciseTimer
        assert overlay.start_magnification(240, 2) == (True, "")
        assert screen.count == 1  # no event-loop/timer wait for the first grab
        assert affinity[0][1] is True
        assert not overlay._magnified_frame.isNull()
        assert overlay._magnification_timer.isActive()
        # Check event-loop updates and replacement of stale frames, not real FPS.
        QTest.qWait(140)
        assert screen.count >= 3
        assert overlay._magnified_frame.toImage().pixelColor(0, 0).red() == screen.count % 256
        overlay.stop_magnification()
        stopped_count = screen.count
        QTest.qWait(40)
        assert screen.count == stopped_count
        assert overlay._magnified_frame.isNull()
        assert affinity[-1][1] is False
    finally:
        overlay.close()


def test_clip_cache_reused_and_rebuilt_on_size_change(app, setup_screen):
    overlay = CrosshairOverlay(CrosshairConfig(), centre=QPoint(400, 300))
    overlay.show()
    try:
        assert overlay.start_magnification(240, 2)[0]
        overlay.grab()
        first = overlay._lens_cache_path
        assert overlay._lens_cache_key == (overlay.width(), 240)
        overlay._capture_magnification()
        overlay.grab()
        assert overlay._lens_cache_path is first
        overlay.set_magnification_options(300, 2)
        overlay.grab()
        assert overlay._lens_cache_key == (overlay.width(), 300)
        assert overlay._lens_cache_path is not first
        assert overlay._lens_cache_rect.width() == 300
    finally:
        overlay.close()


def test_arming_samples_immediately_then_triggers_first_down(app):
    state = [False]
    reads, events = [], []
    poller = magnification.ToggleKeyPoller(reader=lambda key: reads.append(key) or state[0])
    poller.pressed.connect(lambda: events.append("toggle"))
    try:
        assert poller._timer.interval() == 5
        assert poller._timer.timerType() == Qt.TimerType.PreciseTimer
        assert poller.start(0x70)
        assert reads == [0x70] and poller._ready  # no first-timer delay
        state[0] = True
        poller._poll()
        poller._poll()
        assert events == ["toggle"]
        state[0] = False
        poller._poll()
        state[0] = True
        poller._poll()
        assert events == ["toggle", "toggle"]
    finally:
        poller.stop()


def test_key_held_during_arming_requires_release(app):
    state = [True]
    events = []
    poller = magnification.ToggleKeyPoller(reader=lambda _key: state[0])
    poller.pressed.connect(lambda: events.append(1))
    try:
        assert poller.start(0x05)
        poller._poll()
        assert events == []
        state[0] = False
        poller._poll()
        state[0] = True
        poller._poll()
        assert events == [1]
    finally:
        poller.stop()


def test_initial_key_error_fails_closed(app):
    def fail(_key):
        raise OSError("unavailable")

    poller = magnification.ToggleKeyPoller(reader=fail)
    errors = []
    poller.failed.connect(errors.append)
    assert not poller.start(0x70)
    assert not poller._timer.isActive() and poller._key == 0
    assert len(errors) == 1 and "unavailable" in errors[0]
