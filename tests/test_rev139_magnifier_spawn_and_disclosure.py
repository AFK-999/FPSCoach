"""Regression tests for error 8 handling, HWND lifecycle and aligned disclosure.

Injected Win32 calls model ordering; real Windows/DWM behavior requires validation.
"""
from __future__ import annotations

import ctypes
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fps_coach.crosshair import CrosshairConfig
from fps_coach.ui import crosshair_overlay as overlay_module
from fps_coach.ui import magnification, pages_practice
from fps_coach.ui.crosshair_overlay import CrosshairOverlay
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Win32Fake:
    LAYERED = 0x80000

    def __init__(self):
        self.style = self.LAYERED | 0x20 | 0x80
        self.original = self.style
        self.flag = 0
        self.last_error = 0
        self.calls = []
        self.force_error = None
        self.retry_error = None
        self.restore_error = False
        self.verify_value = None

    def GetWindowLongW(self, hwnd, index):
        self.calls.append(("get_style", index, self.style))
        return self.style

    def SetWindowLongW(self, hwnd, index, value):
        self.calls.append(("set_style", index, value))
        if self.restore_error and value == self.original:
            self.last_error = 5
            return 0
        previous, self.style = self.style, value
        return previous

    def SetWindowDisplayAffinity(self, hwnd, flag):
        self.calls.append(("set_affinity", flag, self.style))
        # Error 8 is simulated only for attempts on the layered window.
        error = self.force_error or (8 if self.style & self.LAYERED else self.retry_error)
        if error:
            self.last_error = error
            return 0
        self.flag = flag
        return 1

    def GetWindowDisplayAffinity(self, hwnd, out):
        self.calls.append(("get_affinity", self.style, self.flag))
        value = self.flag if self.verify_value is None else self.verify_value
        ctypes.cast(out, ctypes.POINTER(magnification.wintypes.DWORD)).contents.value = value
        return 1


@pytest.fixture
def win32(monkeypatch):
    user = Win32Fake()
    monkeypatch.setattr(magnification, "windows_available", lambda: True)
    monkeypatch.setattr(magnification, "_user32", lambda: user)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: user.last_error, raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", lambda error: setattr(user, "last_error", error), raising=False)
    return user


def test_error_8_retry_before_layering_and_verify_after_restore(win32):
    ok, message = magnification.set_capture_exclusion(123, True)
    assert ok, message
    assert win32.style == win32.original and win32.flag == 0x11
    assert [x[0] for x in win32.calls] == [
        "set_affinity", "get_style", "set_style", "set_affinity", "set_style", "get_affinity"]
    assert win32.calls[2][2] == win32.original & ~win32.LAYERED
    assert win32.calls[3][2] & win32.LAYERED == 0
    assert win32.calls[-1][1] == win32.original
    # The same approach also restores capture visibility when disable errors.
    assert magnification.set_capture_exclusion(123, False)[0]
    assert win32.flag == 0 and win32.style == win32.original


def test_error8_retry_failure_restores_every_style_bit(win32):
    win32.retry_error = 5
    ok, message = magnification.set_capture_exclusion(123, True)
    assert not ok and "retry" in message and "5" in message
    assert win32.style == win32.original
    assert not any(c[0] == "get_affinity" for c in win32.calls)


def test_style_restore_failure_fails_closed_and_requests_reset(win32):
    win32.restore_error = True
    ok, message = magnification.set_capture_exclusion(123, True)
    assert not ok and "restore" in message and "Restart CurveCoach" in message
    assert win32.flag == 0  # rollback best-effort
    assert not any(c[0] == "get_affinity" for c in win32.calls)


def test_non8_failure_never_rewrites_the_window_style(win32):
    win32.force_error = 5
    ok, message = magnification.set_capture_exclusion(123, True)
    assert not ok and "error 5" in message
    assert [c[0] for c in win32.calls] == ["set_affinity"]
    assert win32.style == win32.original


def test_nonlayered_error8_fails_without_changing_style(win32):
    win32.style = 0xA0
    win32.force_error = 8
    ok, message = magnification.set_capture_exclusion(123, True)
    assert not ok and "not layered" in message
    assert not any(c[0] == "set_style" for c in win32.calls)


def test_retry_never_accepts_unverified_affinity(win32):
    win32.verify_value = 0
    ok, message = magnification.set_capture_exclusion(123, True)
    assert not ok and "confirm" in message
    assert win32.style == win32.original


class FakeScreen:
    def __init__(self):
        self.calls = []

    def geometry(self):
        return QRect(0, 0, 800, 600)

    def availableGeometry(self):
        return self.geometry()

    def grabWindow(self, win, x, y, width, height):
        self.calls.append((win, x, y, width, height))
        image = QPixmap(width, height)
        image.fill(QColor("blue"))
        return image


@pytest.fixture
def screen(app, monkeypatch):
    result = FakeScreen()
    monkeypatch.setattr(overlay_module.QApplication, "primaryScreen", lambda: result)
    return result


def test_spawn_has_correct_center_and_excludes_before_first_capture(app, screen, monkeypatch):
    calls = []
    monkeypatch.setattr(overlay_module, "set_capture_exclusion", lambda hwnd, on: (calls.append((hwnd, on)) or (True, "")))
    overlay = CrosshairOverlay(CrosshairConfig(), centre=QPoint(430, 280))
    overlay.show()
    initial = overlay.centre
    assert overlay.start_magnification(245, 2.75)[0]
    assert calls[0][1] and overlay._excluded_hwnd == int(overlay.winId())
    assert overlay.centre == initial
    assert overlay.geometry().x() + overlay.width() // 2 == initial.x()
    assert overlay.geometry().y() + overlay.height() // 2 == initial.y()
    assert overlay._source_rect.center().x() == pytest.approx(initial.x())
    assert overlay._source_rect.center().y() == pytest.approx(initial.y())
    assert len(screen.calls) == 1 and overlay._magnification_timer.isActive()
    overlay.stop_magnification()
    assert calls[-1][1] is False and overlay.centre == initial
    assert overlay.start_magnification(245, 2.75)[0]
    assert overlay.centre == initial and len(screen.calls) == 2
    overlay.close()


def test_recreated_handle_stops_before_grab(app, screen, monkeypatch):
    calls = []
    monkeypatch.setattr(overlay_module, "set_capture_exclusion", lambda hwnd, on: (calls.append((hwnd, on)) or (True, "")))
    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.show()
    assert overlay.start_magnification(240, 2)[0]
    errors = []
    overlay.magnification_stopped.connect(errors.append)
    overlay._excluded_hwnd += 1  # simulate the HWND being replaced after verification
    assert not overlay._capture_magnification()
    assert not overlay.magnification_enabled and not overlay._magnification_timer.isActive()
    assert len(screen.calls) == 1 and len(calls) == 1
    assert "window changed" in errors[-1]
    overlay.close()


def test_disclosure_starts_closed_and_open_close_do_not_toggle_capture(app, monkeypatch):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: True)
    page = pages_practice.CrosshairPage()
    page.resize(1280, 1100)
    page.show()
    app.processEvents()
    try:
        assert not page.mag_disclosure.isChecked() and page.mag_panel.isHidden()
        assert "Collapsed" in page.mag_disclosure.accessibleDescription()
        page.activation.setChecked(True)
        page.mag_disclosure.setChecked(True)
        app.processEvents()
        assert not page.mag_panel.isHidden() and "Expanded" in page.mag_disclosure.accessibleDescription()
        assert not page.magnification_toggle.isChecked()
        page.magnification_toggle.setChecked(True)
        page.mag_disclosure.setChecked(False)
        assert page.mag_panel.isHidden() and page.magnification_toggle.isChecked()
        page.set_magnification_error("capture error")
        assert page.mag_disclosure.isChecked() and not page.mag_panel.isHidden()
        assert not page.magnification_toggle.isChecked() and "capture error" in page.mag_note.text()
    finally:
        page.close()


def test_expanded_form_control_columns_align(app, monkeypatch):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: True)
    page = pages_practice.CrosshairPage()
    page.resize(1280, 1100)
    page.show()
    page.mag_disclosure.setChecked(True)
    app.processEvents()
    try:
        x = page.preset.mapTo(page, QPoint(0, 0)).x()
        assert page.mag_diameter.mapTo(page, QPoint(0, 0)).x() == x
        assert page.mag_zoom.mapTo(page, QPoint(0, 0)).x() == x
        assert page.mag_binding.mapTo(page, QPoint(0, 0)).x() == x
        assert page.shape.mapTo(page, QPoint(0, 0)).x() == x
        assert page.mag_disclosure.width() == 150
        assert page.magnification_toggle.width() > page.mag_disclosure.width()
    finally:
        page.close()


def test_narrow_wrapped_disclosure_text_is_not_clipped(app, monkeypatch):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: False)
    page = pages_practice.CrosshairPage()
    page.resize(820, 760)
    page.show()
    page.mag_disclosure.setChecked(True)
    for _ in range(4):
        app.processEvents()
    try:
        for label in (page.mag_note, page.mag_warning):
            assert label.height() >= label.heightForWidth(label.width())
        assert page.mag_warning.geometry().top() >= page.mag_note.geometry().bottom()
    finally:
        page.close()
