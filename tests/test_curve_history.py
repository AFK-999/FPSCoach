"""Calibration curve undo/redo, the current-vs-older legend, and the chat thinking indicator.

Undo and Redo track in-session curve edits only. They are not the saved calibration
history, which was reverted in rev15 and must not come back.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP_ALIVE: list = []  # Qt widgets are torn down in a fixed order when kept until exit


def _calibration_page(qapp):
    from fps_coach.ui.pages_setup import CalibrationPage

    from types import SimpleNamespace

    # The curve history paths never use the backend; only its analyzer is read at construction.
    page = CalibrationPage(SimpleNamespace(analyzer=None))
    page._set_chart([0.0, 0.3, 0.55, 0.78, 1.0])
    _KEEP_ALIVE.append(page)
    return page


def _drag(page, points):
    """Mimic a finished drag: the chart points move, then curve_adjusted fires on release."""
    page.chart.points = list(points)
    page._on_curve_adjusted(list(points))


# -- undo / redo ----------------------------------------------------------------

def test_undo_is_disabled_until_there_is_a_change(qapp) -> None:
    page = _calibration_page(qapp)
    assert not page.undo_btn.isEnabled()
    assert not page.redo_btn.isEnabled()


def test_undo_then_redo_restores_each_drag(qapp) -> None:
    page = _calibration_page(qapp)
    start = list(page.chart.points)
    edited = [0.0, 0.4, 0.55, 0.78, 1.0]

    _drag(page, edited)
    assert page.undo_btn.isEnabled()
    assert not page.redo_btn.isEnabled()

    page.undo()
    assert page.chart.points == start
    assert not page.undo_btn.isEnabled()
    assert page.redo_btn.isEnabled()

    page.redo()
    assert page.chart.points == edited
    assert page.undo_btn.isEnabled()
    assert not page.redo_btn.isEnabled()


def test_undo_button_click_runs_undo(qapp) -> None:
    page = _calibration_page(qapp)
    start = list(page.chart.points)
    _drag(page, [0.0, 0.1, 0.2, 0.3, 0.4])
    page.undo_btn.click()
    assert page.chart.points == start
    page.redo_btn.click()
    assert page.chart.points == [0.0, 0.1, 0.2, 0.3, 0.4]


def test_new_change_after_undo_clears_redo(qapp) -> None:
    page = _calibration_page(qapp)
    _drag(page, [0.0, 0.2, 0.5, 0.8, 1.0])
    page.undo()
    assert page.redo_btn.isEnabled()
    _drag(page, [0.0, 0.25, 0.5, 0.8, 1.0])
    assert not page.redo_btn.isEnabled()


def test_click_without_movement_adds_no_history(qapp) -> None:
    page = _calibration_page(qapp)
    _drag(page, list(page.chart.points))  # no movement
    assert page._undo == []
    assert not page.undo_btn.isEnabled()


def test_history_is_capped(qapp) -> None:
    page = _calibration_page(qapp)
    for i in range(page._HISTORY_LIMIT + 10):
        value = round(0.2 + (i % 50) * 0.01, 3)  # always a new value, so each drag counts
        _drag(page, [0.0, value, 0.55, 0.78, 1.0])
    assert len(page._undo) == page._HISTORY_LIMIT


def test_restore_emits_profile_changed_and_base_only_when_present(qapp) -> None:
    page = _calibration_page(qapp)
    profiles, bases = [], []
    page.profile_changed.connect(profiles.append)
    page.base_profile_changed.connect(bases.append)

    _drag(page, [0.0, 0.4, 0.55, 0.78, 1.0])
    page.undo()
    assert profiles[-1] == {"restored": True}
    assert bases == []  # no imported base curve, so nothing to report

    page._base_profile = {"name": "imported"}
    _drag(page, [0.0, 0.45, 0.55, 0.78, 1.0])
    bases.clear()
    page.undo()
    assert bases == [{"name": "imported"}]


def test_undo_keyboard_shortcuts_are_registered(qapp) -> None:
    from PySide6.QtGui import QShortcut

    page = _calibration_page(qapp)
    texts = {s.key().toString() for s in page.findChildren(QShortcut)}
    assert "Ctrl+Z" in texts
    assert "Ctrl+Y" in texts


# -- legend ---------------------------------------------------------------------

def test_legend_reports_whether_an_older_curve_exists(qapp) -> None:
    from fps_coach.ui.widgets import CurveLegend

    legend = CurveLegend()
    assert legend._has_older is False
    assert legend.grab().width() > 0
    legend.set_has_older(True)
    assert legend._has_older is True
    assert legend.grab().width() > 0


def test_calibration_legend_follows_previous_curve(qapp) -> None:
    page = _calibration_page(qapp)
    assert page.legend._has_older is False
    page._set_chart([0.0, 0.2, 0.5, 0.9, 1.0], previous=[0.0, 0.3, 0.55, 0.78, 1.0])
    assert page.legend._has_older is True


# -- chat thinking indicator ----------------------------------------------------

def test_thinking_indicator_shows_and_hides_with_requests(qapp) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    assert page._thinking.isHidden()
    page._request_started()
    assert not page._thinking.isHidden()
    assert page.transcript.viewportMargins().bottom() == page._THINKING_STRIP

    # Two overlapping requests: the indicator stays until both finish.
    page._request_started()
    page._request_finished()
    assert not page._thinking.isHidden()
    page._request_finished()
    assert page._thinking.isHidden()
    assert page.transcript.viewportMargins().bottom() == 0


def test_thinking_indicator_clears_after_a_failed_request(qapp) -> None:
    """A request that fails still ends, so the indicator must go away."""
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page._request_started()
    page._request_finished()  # what the finished signal triggers on error too
    assert page._thinking.isHidden()


def test_thinking_counter_never_goes_negative(qapp) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page._request_finished()
    assert page._pending_requests == 0
    assert page._thinking.isHidden()


def test_thinking_indicator_animation_follows_effects_setting(qapp) -> None:
    from fps_coach.ui.pages_coach import ThinkingIndicator
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    host.resize(400, 200)
    animated = ThinkingIndicator(host, animate=True)
    animated.start()
    assert animated._timer.isActive()
    before = animated._phase
    animated._tick()
    assert animated._phase != before
    animated.stop()
    assert not animated._timer.isActive()
    assert animated.isHidden()

    still = ThinkingIndicator(host, animate=False)
    still.start()
    assert not still._timer.isActive()  # performance mode: dots are drawn still
    assert not still.isHidden()
    assert still.grab().width() > 0
    still.stop()


def test_performance_mode_disables_thinking_animation(qapp) -> None:
    from fps_coach.ui.styles import fx_enabled

    assert fx_enabled("luxurious") is True
    assert fx_enabled("performance") is False
