"""Crosshair Activation: the overlay is off by default, is click-through, and follows the setting."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def clean_setting(qapp):
    from fps_coach.ui.widgets import _settings

    s = _settings()
    previous = s.value("crosshair/overlay_active", False, type=bool)
    s.setValue("crosshair/overlay_active", False)
    yield s
    s.setValue("crosshair/overlay_active", previous)


def test_overlay_window_flags_and_click_through(qapp) -> None:
    from PySide6.QtCore import Qt

    from fps_coach.crosshair import CrosshairConfig
    from fps_coach.ui.crosshair_overlay import CrosshairOverlay

    overlay = CrosshairOverlay(CrosshairConfig())
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    overlay.deleteLater()


def test_overlay_size_grows_with_crosshair(qapp) -> None:
    from fps_coach.crosshair import CrosshairConfig
    from fps_coach.ui.crosshair_overlay import overlay_side

    small = overlay_side(CrosshairConfig(size=4, gap=2, thickness=1, dot_size=1))
    large = overlay_side(CrosshairConfig(size=60, gap=30, thickness=8, dot_size=10))
    assert small >= 32
    assert large > small


def test_activation_defaults_off_and_persists(qapp, clean_setting) -> None:
    from fps_coach.ui.pages_practice import CrosshairPage

    page = CrosshairPage()
    assert not page.activation.isChecked()
    assert "Off" in page.activation.text()

    seen: list[bool] = []
    page.activation_changed.connect(seen.append)
    page.activation.setChecked(True)
    assert seen == [True]
    assert clean_setting.value("crosshair/overlay_active", False, type=bool) is True
    assert "On" in page.activation.text()

    page.activation.setChecked(False)
    assert seen == [True, False]
    assert clean_setting.value("crosshair/overlay_active", True, type=bool) is False


def test_config_changes_are_signalled(qapp, clean_setting) -> None:
    from fps_coach.ui.pages_practice import CrosshairPage

    page = CrosshairPage()
    seen: list = []
    page.config_changed.connect(seen.append)
    # Pick a value that differs from the saved one, so the change is real.
    target = page.thickness.value() % 12 + 1
    page.thickness.setValue(target)
    assert seen, "changing a slider must emit config_changed"
    assert seen[-1].thickness == target


def test_main_window_overlay_follows_activation(qapp, clean_setting) -> None:
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        assert window._overlay is None  # off by default: no overlay created
        window.crosshair_page.activation.setChecked(True)
        assert window._overlay is not None
        assert window._overlay.isVisible()
        window.crosshair_page.activation.setChecked(False)
        assert not window._overlay.isVisible()
    finally:
        window.close()
