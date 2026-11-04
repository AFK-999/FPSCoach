"""Rounded title styling, bundled Inter wordmark, and one-shot startup glow."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QAbstractAnimation, QSettings, Qt
from PySide6.QtGui import QFontInfo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import dynamic_curve, main_window

    def create(theme="dark", animations=True, mode="luxurious"):
        settings = QSettings(str(tmp_path / f"{theme}-{animations}-{mode}.ini"), QSettings.Format.IniFormat)
        settings.setValue("ui/theme", theme)
        settings.setValue("ui/animations", animations)
        settings.setValue("ui/mode", mode)
        monkeypatch.setattr(main_window, "_settings", lambda: settings)
        monkeypatch.setattr(dynamic_curve, "_settings", lambda: settings)
        monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
        win = main_window.MainWindow()
        _KEEP.append(win)
        win.resize(1400, 900)
        win.show()
        QTest.qWait(20)
        return win
    return create


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_title_is_rounded_and_brand_uses_bundled_inter(make_window, theme):
    from fps_coach.ui.brand_font import load_brand_font

    w = make_window(theme)
    bar = w.title_bar
    assert "QFrame#appTitleBar" in w.styleSheet() and "border-radius: 8px" in w.styleSheet()
    assert bar.minimize_button.x() < bar.fullscreen_button.x() < bar.close_button.x()
    assert bar.close_button.isVisible() and not w.mask().isEmpty()
    im = w.grab().toImage()
    assert im.pixelColor(bar.x(), bar.y()) != im.pixelColor(bar.x() + bar.width() // 2, bar.y() + bar.height() // 2)
    label = next(x for x in w.findChildren(QLabel) if x.objectName() == "brand")
    assert label.text() == "CURVECOACH" and load_brand_font()
    assert QFontInfo(label.font()).family() == "Inter"
    w.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_startup_effect_is_one_shot_nonblocking_and_finishes(make_window, theme):
    w = make_window(theme)
    glow = w.startup_glow
    assert not glow.isVisible()
    assert w.play_startup_animation()
    assert glow.isVisible() and glow.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert glow.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert glow._animation.state() == QAbstractAnimation.State.Running
    tab = w._nav_buttons["chat"]
    point = tab.mapTo(w.centralWidget(), tab.rect().center())
    assert w.centralWidget().childAt(point) is not glow
    # Different animation phases produce different rendered ribbons.
    glow._animation.pause()
    glow._set_progress(0.30)
    image_a = glow.grab().toImage()
    glow._set_progress(0.55)
    image_b = glow.grab().toImage()
    assert image_a != image_b
    w._nav_buttons["chat"].click()
    assert w.stack.currentWidget() is w.chat_page and glow.isVisible()
    glow._animation.resume()
    glow._animation.setCurrentTime(glow.DURATION_MS)
    QTest.qWait(10)
    assert not glow.isVisible() and glow._animation.state() == QAbstractAnimation.State.Stopped
    assert not w.play_startup_animation()
    w.close()


@pytest.mark.parametrize("animations,mode", [(False, "luxurious"), (True, "performance")])
def test_animation_respects_preferences_and_does_not_replay(make_window, animations, mode):
    w = make_window(animations=animations, mode=mode)
    assert not w.play_startup_animation()
    assert not w.startup_glow.isVisible()
    w.s.setValue("ui/animations", True)
    w.s.setValue("ui/mode", "luxurious")
    assert not w.play_startup_animation()
    w.close()


def test_turning_animations_off_stops_in_flight_glow(make_window):
    w = make_window()
    assert w.play_startup_animation()
    w.s.setValue("ui/animations", False)
    w._apply_theme()
    assert not w.startup_glow.isVisible()
    assert w.startup_glow._animation.state() == QAbstractAnimation.State.Stopped
    w.close()


def test_effect_resizes_and_raw_capture_does_not_pause(make_window, monkeypatch):
    from fps_coach.ui import raw_mouse_capture
    from test_raw_calibration import FakeAPI

    api = FakeAPI()
    original = raw_mouse_capture.RawMouseCapture
    monkeypatch.setattr(raw_mouse_capture, "RawMouseCapture", lambda arena, move, click, lost, **kw:
                        original(arena, move, click, lost, api=api, **kw))
    w = make_window()
    assert w.play_startup_animation()
    w.resize(1500, 920)
    QTest.qWait(20)
    assert w.startup_glow.geometry() == w.centralWidget().rect()
    w._select_page("calibration")
    canvas = w.calibration_page.canvas
    canvas.set_input_mode("raw")
    QTest.qWait(50)
    canvas.start_session()
    assert canvas._active and canvas._raw_capture is not None and api.rect is not None
    original_size = canvas.size()
    w.startup_glow._set_progress(0.6)
    QTest.qWait(20)
    assert canvas._raw_capture is not None and api.rect is not None and canvas.size() == original_size
    canvas.cancel_session()
    w.close()


def test_app_entry_starts_effect_once_after_show(monkeypatch):
    import fps_coach.app as app_module

    calls = []
    class FakeApplication:
        @staticmethod
        def setHighDpiScaleFactorRoundingPolicy(_value):
            pass
        def __init__(self, _args):
            pass
        def setApplicationDisplayName(self, _value):
            pass
        def setApplicationName(self, _value):
            pass
        def setOrganizationName(self, _value):
            pass
        def setFont(self, _value):
            pass
        def setStyleSheet(self, _value):
            pass
        def exec(self):
            calls.append("exec")
            return 0
    class FakeWindow:
        def __init__(self, _backend):
            pass
        def show(self):
            calls.append("show")
        def play_startup_animation(self):
            calls.append("startup")
    monkeypatch.setattr(app_module, "QApplication", FakeApplication)
    monkeypatch.setattr(app_module, "MainWindow", FakeWindow)
    monkeypatch.setattr(app_module, "CoreBackend", lambda: object())
    monkeypatch.setattr(app_module, "ensure_environment", lambda: None)
    monkeypatch.setattr(app_module, "ensure_running_async", lambda: None)
    assert app_module.main() == 0
    assert calls == ["show", "startup", "exec"]


def test_missing_font_is_a_nonfatal_fallback(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import brand_font

    monkeypatch.setattr(brand_font, "_font_loaded", False)
    monkeypatch.setattr(brand_font, "Path", lambda _path: tmp_path / "missing" / "stub.py")
    assert not brand_font.load_brand_font()


def test_closing_stops_the_animation(make_window):
    w = make_window()
    assert w.play_startup_animation()
    w.close()
    assert not w.startup_glow.isVisible()
    assert w.startup_glow._animation.state() == QAbstractAnimation.State.Stopped
