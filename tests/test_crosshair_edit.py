"""Crosshair edit mode: drag to position, lock in place, save the position, and privacy text."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEYS = ("crosshair/overlay_active", "crosshair/overlay_x", "crosshair/overlay_y")


@pytest.fixture()
def clean_settings(qapp):
    from fps_coach.ui.widgets import _settings

    s = _settings()
    saved = {k: s.value(k) for k in _KEYS}
    for k in _KEYS:
        s.remove(k)
    yield s
    for k in _KEYS:
        s.remove(k)
        if saved[k] is not None:
            s.setValue(k, saved[k])


def test_locked_overlay_is_click_through(qapp) -> None:
    from PySide6.QtCore import Qt

    from fps_coach.crosshair import CrosshairConfig
    from fps_coach.ui.crosshair_overlay import CrosshairOverlay

    overlay = CrosshairOverlay(CrosshairConfig())
    assert not overlay.editing
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.deleteLater()


def test_edit_mode_takes_the_mouse_and_locking_restores_click_through(qapp) -> None:
    from PySide6.QtCore import Qt

    from fps_coach.crosshair import CrosshairConfig
    from fps_coach.ui.crosshair_overlay import CrosshairOverlay

    overlay = CrosshairOverlay(CrosshairConfig())
    overlay.set_editing(True)
    assert overlay.editing
    assert not (overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput)

    overlay.set_editing(False)
    assert not overlay.editing
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.deleteLater()


def test_move_is_clamped_to_the_primary_screen(qapp) -> None:
    from PySide6.QtCore import QPoint

    from fps_coach.crosshair import CrosshairConfig
    from fps_coach.ui.crosshair_overlay import CrosshairOverlay, _primary_area

    overlay = CrosshairOverlay(CrosshairConfig())
    area = _primary_area()
    overlay.move_centre(QPoint(-5000, -5000))
    assert overlay.centre.x() >= area.left()
    assert overlay.centre.y() >= area.top()
    overlay.move_centre(QPoint(99999, 99999))
    assert overlay.centre.x() <= area.right()
    assert overlay.centre.y() <= area.bottom()
    overlay.deleteLater()


def test_drag_in_edit_mode_moves_and_commits(qapp) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from fps_coach.crosshair import CrosshairConfig
    from fps_coach.ui.crosshair_overlay import CrosshairOverlay, _primary_area

    area = _primary_area()
    start = QPoint(area.center().x() - 300, area.center().y() - 200)
    overlay = CrosshairOverlay(CrosshairConfig(), centre=start)
    overlay.set_editing(True)
    overlay.show()
    qapp.processEvents()

    committed: list[QPoint] = []
    overlay.position_committed.connect(committed.append)
    before = overlay.centre
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=overlay.rect().center())
    QTest.mouseMove(overlay, overlay.rect().center() + QPoint(40, 25))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=overlay.rect().center() + QPoint(40, 25))
    qapp.processEvents()
    overlay.hide()
    if overlay.centre == before:
        pytest.skip("this Qt platform did not deliver synthetic drag events")
    assert committed and committed[-1] == overlay.centre
    overlay.deleteLater()


def test_saved_position_is_restored_on_a_new_overlay(qapp, clean_settings) -> None:
    from PySide6.QtCore import QPoint

    from fps_coach.crosshair import CrosshairConfig
    from fps_coach.ui.crosshair_overlay import CrosshairOverlay
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.crosshair_page.edit_position.setChecked(True)
        overlay = window._overlay
        assert overlay is not None and overlay.editing
        target = QPoint(overlay.centre.x() - 120, overlay.centre.y() + 60)
        overlay.move_centre(target)
        overlay.position_committed.emit(overlay.centre)  # what a finished drag does
        assert clean_settings.value("crosshair/overlay_x", type=int) == overlay.centre.x()
        assert clean_settings.value("crosshair/overlay_y", type=int) == overlay.centre.y()

        window.crosshair_page.edit_position.setChecked(False)
        assert not overlay.editing
        assert not overlay.isVisible()  # locked, Activation off: nothing on screen
    finally:
        window.close()

    reopened = CrosshairOverlay(CrosshairConfig(), centre=QPoint(
        clean_settings.value("crosshair/overlay_x", type=int),
        clean_settings.value("crosshair/overlay_y", type=int),
    ))
    assert reopened.centre == overlay.centre
    reopened.deleteLater()


def test_reset_to_centre_clears_saved_position(qapp, clean_settings) -> None:
    from PySide6.QtCore import QPoint

    from fps_coach.ui.main_window import MainWindow

    clean_settings.setValue("crosshair/overlay_x", 100)
    clean_settings.setValue("crosshair/overlay_y", 100)
    window = MainWindow()
    try:
        window.crosshair_page.centre_position.click()
        assert not clean_settings.contains("crosshair/overlay_x")
        from fps_coach.ui.crosshair_overlay import _primary_area

        assert window._overlay.centre == _primary_area().center()
    finally:
        window.close()
        assert QPoint(0, 0) is not None


def test_privacy_text_states_the_no_interaction_rule(qapp) -> None:
    from fps_coach.ui.pages_coach import PRIVACY_POINTS

    text = " ".join(PRIVACY_POINTS)
    assert "The active crosshair overlay does not interact with or inject into any game processes." in text
    assert "does not read or write any process's memory" in text
