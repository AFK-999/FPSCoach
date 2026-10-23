"""The top bar must not overlap at the smallest window width.

The window's minimum width is the top bar's measured need, taken after the stylesheet is applied.
Before the fix it was measured at construction (21 px too small), so the tab strip ran under the
core-status dot between 1120 and 1243 px.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(tmp_path, monkeypatch, qapp):
    from PySide6.QtCore import QSettings

    from fps_coach.ui import main_window, pages_coach, pages_setup, widgets

    settings = QSettings(str(tmp_path / "topbar.ini"), QSettings.Format.IniFormat)
    for module in (main_window, pages_setup, pages_coach, widgets):
        if hasattr(module, "_settings"):
            monkeypatch.setattr(module, "_settings", lambda: settings)
    win = main_window.MainWindow()
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    qapp.processEvents()


def _bar_need(win) -> int:
    return win._topbar_frame.layout().totalMinimumSize().width()


def _overlaps(win) -> list[str]:
    from PySide6.QtWidgets import QFrame

    bar = win._topbar_frame
    scroll = bar.findChild(QFrame, "navScroll")
    problems = []
    if win.core_status.geometry().x() < scroll.geometry().right():
        problems.append("core dot under the tab strip")
    for button in win._util_buttons.values():
        if button.geometry().x() < scroll.geometry().right():
            problems.append("utility button under the tab strip")
        if button.geometry().right() > bar.width():
            problems.append("utility button past the bar edge")
    return problems


def test_minimum_width_matches_the_measured_top_bar_need(window) -> None:
    assert window.minimumWidth() == max(1120, _bar_need(window))


def test_narrowest_window_has_no_top_bar_overlap(window, qapp) -> None:
    window.resize(1120, 680)
    qapp.processEvents()
    assert window.width() >= _bar_need(window)
    assert _overlaps(window) == []


def test_minimum_width_follows_a_theme_change(window, qapp) -> None:
    window.s.setValue("ui/theme", "light")
    window._apply_theme()
    qapp.processEvents()
    assert window.minimumWidth() == max(1120, _bar_need(window))
    window.resize(1120, 680)
    qapp.processEvents()
    assert _overlaps(window) == []
