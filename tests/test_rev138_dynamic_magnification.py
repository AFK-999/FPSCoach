"""Desktop magnifier behavior with injected screen and Windows adapters.

These tests do not validate the Windows compositor or real games.
"""
from __future__ import annotations

import ctypes
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fps_coach.crosshair import CrosshairConfig
from fps_coach.ui import crosshair_overlay as overlay_module
from fps_coach.ui import magnification, main_window, pages_practice
from fps_coach.ui.crosshair_overlay import CrosshairOverlay
from fps_coach.ui.widgets import _settings
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clean_settings(app):
    settings = _settings()
    keys = ("crosshair/overlay_active", "crosshair/overlay_x", "crosshair/overlay_y",
            "crosshair/magnification_diameter", "crosshair/magnification_zoom_pct",
            "crosshair/magnification_binding")
    saved = {k: settings.value(k) for k in keys}
    for k in keys:
        settings.remove(k)
    yield settings
    for k in keys:
        settings.remove(k)
        if saved[k] is not None:
            settings.setValue(k, saved[k])


class FakeScreen:
    def __init__(self, *, fail=False, dpr=2):
        self.calls = []
        self.fail = fail
        self.dpr = dpr

    def geometry(self):
        return QRect(0, 0, 800, 600)

    def availableGeometry(self):
        return self.geometry()

    def grabWindow(self, win, x, y, width, height):
        self.calls.append((win, x, y, width, height))
        if self.fail:
            return QPixmap()
        pixmap = QPixmap(width * self.dpr, height * self.dpr)
        pixmap.fill(QColor("red"))
        pixmap.setDevicePixelRatio(self.dpr)
        return pixmap


@pytest.fixture
def fake_screen(app, monkeypatch):
    screen = FakeScreen()
    monkeypatch.setattr(overlay_module.QApplication, "primaryScreen", lambda: screen)
    return screen


@pytest.fixture
def affinity(monkeypatch):
    calls = []
    monkeypatch.setattr(overlay_module, "set_capture_exclusion",
                        lambda hwnd, enable: (calls.append((hwnd, enable)) or (True, "")))
    return calls


def test_binding_codes_are_unique_and_valid():
    assert len(magnification.BINDINGS) == len(magnification.ALLOWED_KEYS)
    assert ("Mouse · left (physical)", 1) in magnification.BINDINGS
    assert ("Keyboard · F24", 0x87) in magnification.BINDINGS


def test_poller_release_then_down_edge_and_no_consumption(app):
    events, state, calls = [], [True], []
    poller = magnification.ToggleKeyPoller(reader=lambda key: calls.append(key) or state[0])
    poller.pressed.connect(lambda: events.append("toggle"))
    assert not poller.start(0)
    assert not poller.start(0xFFFF)
    assert poller.start(0x70)
    poller._poll()  # held on arm: do not trigger
    assert events == []
    state[0] = False
    poller._poll()
    state[0] = True
    poller._poll()
    poller._poll()
    assert events == ["toggle"]
    state[0] = False
    poller._poll()
    state[0] = True
    poller._poll()
    assert events == ["toggle", "toggle"]
    assert set(calls) == {0x70}
    poller.stop()
    poller._poll()
    assert poller._key == 0 and not poller._timer.isActive()


def test_poller_failure_disarms_itself(app):
    def bad_reader(_key):
        raise OSError("no desktop")
    poller = magnification.ToggleKeyPoller(reader=bad_reader)
    errors = []
    poller.failed.connect(errors.append)
    poller.start(0x05)
    poller._poll()
    assert "no desktop" in errors[0]
    assert not poller._timer.isActive() and poller._key == 0


def test_windows_exclusion_verified_and_restored(monkeypatch):
    class User:
        value = 0
        accept = True
        def SetWindowDisplayAffinity(self, _hwnd, flag):
            self.value = flag
            return True
        def GetWindowDisplayAffinity(self, _hwnd, out):
            ctypes.cast(out, ctypes.POINTER(magnification.wintypes.DWORD)).contents.value = self.value if self.accept else 1
            return True
    user = User()
    monkeypatch.setattr(magnification, "windows_available", lambda: True)
    monkeypatch.setattr(magnification, "_user32", lambda: user)
    assert magnification.set_capture_exclusion(123, True) == (True, "")
    assert user.value == 0x11
    assert magnification.set_capture_exclusion(123, False) == (True, "")
    user.accept = False
    ok, error = magnification.set_capture_exclusion(123, True)
    assert not ok and "confirm" in error and user.value == 0


def test_exclusion_failure_prevents_any_capture(app, fake_screen, monkeypatch):
    monkeypatch.setattr(overlay_module, "set_capture_exclusion", lambda *_: (False, "exclusion denied"))
    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.show()
    ok, message = overlay.start_magnification(240, 2)
    assert not ok and message == "exclusion denied"
    assert not overlay.magnification_enabled and not fake_screen.calls
    assert not overlay._magnification_timer.isActive()
    overlay.close()


def test_capture_geometry_fractional_zoom_edge_clip_and_dpr(app, fake_screen, affinity):
    overlay = CrosshairOverlay(CrosshairConfig(), centre=QPoint(40, 40))
    overlay.show()
    before = overlay.centre
    ok, message = overlay.start_magnification(241, 2.35)
    assert ok, message
    assert overlay.centre == before
    assert overlay.width() % 2 == 0
    assert overlay.geometry().x() + overlay.width() // 2 == before.x()
    assert overlay._source_rect.center().x() == pytest.approx(before.x())
    assert overlay._source_rect.center().y() == pytest.approx(before.y())
    assert overlay._source_rect.width() == pytest.approx(241 / 2.35)
    assert fake_screen.calls[0][1:3] == (0, 0)  # partly off the primary screen
    assert overlay._magnified_frame.devicePixelRatio() == 1
    assert overlay._magnified_frame.width() == fake_screen.calls[0][3] * 2
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert affinity[0][1] is True
    overlay.stop_magnification()
    assert affinity[-1][1] is False
    assert overlay.centre == before
    assert overlay._magnified_frame.isNull()
    assert not overlay._magnification_timer.isActive()
    n = len(fake_screen.calls)
    assert not overlay._capture_magnification() and len(fake_screen.calls) == n
    overlay.close()


def test_circle_clips_pixmap_and_crosshair_remains_visible(app, fake_screen, affinity):
    overlay = CrosshairOverlay(CrosshairConfig(), centre=QPoint(400, 300))
    overlay.show()
    assert overlay.start_magnification(240, 2)[0]
    app.processEvents()
    img = overlay.grab().toImage()
    assert img.pixelColor(10, 10).alpha() == 0  # outside circle
    assert img.pixelColor(overlay.width() // 2 + 30, overlay.height() // 2).red() > 200
    overlay.close()


def test_fractional_zoom_scales_hidpi_frame_about_exact_center(app, monkeypatch, affinity):
    class GradientScreen(FakeScreen):
        def grabWindow(self, win, x, y, width, height):
            pixmap = QPixmap(width * 2, height * 2)
            painter = QPainter(pixmap)
            for i in range(width):
                painter.fillRect(i * 2, 0, 2, height * 2, QColor((x + i) % 256, 0, 0))
            painter.end()
            pixmap.setDevicePixelRatio(2)
            return pixmap
    screen = GradientScreen()
    monkeypatch.setattr(overlay_module.QApplication, "primaryScreen", lambda: screen)
    overlay = CrosshairOverlay(CrosshairConfig(), centre=QPoint(400, 300))
    overlay.show()
    assert overlay.start_magnification(241, 2.35)[0]
    app.processEvents()
    image = overlay.grab().toImage()
    centre = overlay.width() // 2
    # Forty-seven lens pixels to the right samples about 20 source pixels
    # to the right, not 40 high-DPI physical pixels or an offset rectangle.
    assert image.pixelColor(centre + 47, centre).red() == pytest.approx(420 % 256, abs=2)
    overlay.close()


def test_runtime_grab_exception_stops_and_reports(app, fake_screen, affinity, monkeypatch):
    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.show()
    assert overlay.start_magnification(240, 2)[0]
    messages = []
    overlay.magnification_stopped.connect(messages.append)
    def fail(*_args):
        raise OSError("screen disconnected")
    monkeypatch.setattr(fake_screen, "grabWindow", fail)
    assert not overlay._capture_magnification()
    assert not overlay.magnification_enabled and not overlay._magnification_timer.isActive()
    assert "screen disconnected" in messages[-1] and affinity[-1][1] is False
    overlay.close()


def test_capture_failure_stops_and_restores(app, fake_screen, affinity):
    fake_screen.fail = True
    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.show()
    messages = []
    overlay.magnification_stopped.connect(messages.append)
    ok, message = overlay.start_magnification(240, 2)
    assert not ok and "capture failed" in message.lower()
    assert messages and affinity[-1][1] is False
    assert not overlay.magnification_enabled and overlay._magnified_frame.isNull()
    assert not overlay._magnification_timer.isActive()
    overlay.close()


def test_edit_and_hide_stop_capture(app, fake_screen, affinity):
    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.show()
    assert overlay.start_magnification(240, 2)[0]
    overlay.set_editing(True)
    assert not overlay.magnification_enabled and not overlay._magnification_timer.isActive()
    overlay.set_editing(False)
    assert overlay.start_magnification(240, 2)[0]
    overlay.hide()
    assert not overlay.magnification_enabled and overlay._magnified_frame.isNull()
    assert affinity[-1][1] is False
    overlay.close()


def test_options_change_captures_new_size(app, fake_screen, affinity):
    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.show()
    assert overlay.start_magnification(240, 2)[0]
    old = len(fake_screen.calls)
    overlay.set_magnification_options(300, 3)
    assert len(fake_screen.calls) == old + 1
    assert overlay._source_rect.width() == pytest.approx(100)
    overlay.close()


def test_preferences_persist_but_active_and_arm_do_not(app, clean_settings, monkeypatch):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: True)
    page = pages_practice.CrosshairPage()
    page.activation.setChecked(True)
    page.mag_diameter.setValue(321)
    page.mag_zoom.setValue(275)
    page.mag_binding.setCurrentIndex(page.mag_binding.findData(0x05))
    page.mag_hotkey_arm.setChecked(True)
    page.magnification_toggle.setChecked(True)
    page2 = pages_practice.CrosshairPage()
    assert page2.mag_diameter.value() == 321 and page2.mag_zoom.value() == 275
    assert page2.mag_binding.currentData() == 0x05
    assert not page2.magnification_toggle.isChecked() and not page2.mag_hotkey_arm.isChecked()
    assert "magnification" not in page2._model.to_dict()
    page.close()
    page2.close()


def test_main_lifecycle_binding_edit_and_hide(app, clean_settings, monkeypatch, fake_screen, affinity):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: True)
    monkeypatch.setattr(main_window, "windows_available", lambda: True)
    win = main_window.MainWindow()
    p = win.crosshair_page
    try:
        p.activation.setChecked(True)
        p.mag_binding.setCurrentIndex(p.mag_binding.findData(0x05))
        assert not win._magnification_keys._timer.isActive()
        p.mag_disclosure.setChecked(True)
        win._magnification_keys._reader = lambda _key: False
        p.mag_hotkey_arm.setChecked(True)
        p.mag_disclosure.setChecked(False)
        assert p.mag_panel.isHidden() and win._magnification_keys._timer.isActive()
        win._magnification_keys._reader = lambda _key: False
        win._magnification_keys._poll()  # release first
        win._magnification_keys._reader = lambda _key: True
        win._magnification_keys._poll()
        assert p.magnification_toggle.isChecked() and win._overlay.magnification_enabled
        assert p.mag_panel.isHidden()  # Key works independently of settings visibility.
        win._magnification_keys._reader = lambda _key: False
        win._magnification_keys._poll()
        win._magnification_keys._reader = lambda _key: True
        win._magnification_keys._poll()
        assert not p.magnification_toggle.isChecked() and not win._overlay.magnification_enabled
        win._magnification_keys._reader = lambda _key: False
        win._magnification_keys._poll()
        win._magnification_keys._reader = lambda _key: True
        win._magnification_keys._poll()
        assert p.magnification_toggle.isChecked() and win._overlay.magnification_enabled
        win._overlay.hide()
        assert not win._magnification_keys._timer.isActive() and not p.magnification_toggle.isChecked()
        win._overlay.show()
        assert win._magnification_keys._timer.isActive()
        p.magnification_toggle.setChecked(True)
        p.edit_position.setChecked(True)
        assert not p.magnification_toggle.isChecked() and not win._overlay.magnification_enabled
        assert not win._magnification_keys._timer.isActive()
        p.edit_position.setChecked(False)
        assert win._magnification_keys._timer.isActive()
        p.magnification_toggle.setChecked(True)
        p.activation.setChecked(False)
        assert not p.magnification_toggle.isChecked() and not win._overlay.isVisible()
        assert not win._magnification_keys._timer.isActive()
    finally:
        win.close()


def test_failure_message_and_hotkey_failure_disarms(app, clean_settings, monkeypatch, fake_screen):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: True)
    monkeypatch.setattr(main_window, "windows_available", lambda: True)
    monkeypatch.setattr(overlay_module, "set_capture_exclusion", lambda *_: (False, "excluded failure"))
    win = main_window.MainWindow()
    p = win.crosshair_page
    try:
        p.activation.setChecked(True)
        p.magnification_toggle.setChecked(True)
        assert not p.magnification_toggle.isChecked() and "excluded failure" in p.mag_note.text()
        assert not fake_screen.calls
        p.mag_binding.setCurrentIndex(p.mag_binding.findData(0x70))
        p.mag_hotkey_arm.setChecked(True)
        win._on_magnification_key_failure("key reader failed")
        assert not p.mag_hotkey_arm.isChecked() and "failed" in p.mag_note.text()
        assert not win._magnification_keys._timer.isActive()
    finally:
        win.close()


def test_unsupported_platform_is_explained(app, clean_settings, monkeypatch):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: False)
    page = pages_practice.CrosshairPage()
    page.activation.setChecked(True)
    assert not page.magnification_toggle.isEnabled()
    assert "Windows" in page.mag_note.text()
    page.close()
