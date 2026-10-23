"""Serious mode: the dashboard tiles bounce around the dashboard, and the speed is set in Settings.

Positions are checked by running the bounce step directly with a fixed frame time, so the tests do
not depend on how fast the sandbox runs.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _FixedClock:
    """Stands in for QElapsedTimer: every frame takes exactly ``ms`` milliseconds."""

    def __init__(self, ms: int = 16):
        self.ms = ms

    def restart(self) -> int:
        return self.ms

    def start(self) -> None:
        pass


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A throwaway settings file for the dashboard and Settings pages."""
    from PySide6.QtCore import QSettings

    from fps_coach.ui import main_window, pages_coach, pages_setup, widgets

    path = tmp_path / "serious.ini"
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    for module in (main_window, pages_setup, pages_coach, widgets):
        if hasattr(module, "_settings"):
            monkeypatch.setattr(module, "_settings", lambda: settings)
    return settings


@pytest.fixture()
def shown_dashboard(qapp, store):
    from fps_coach.ui.pages_setup import DashboardPage

    page = DashboardPage()
    page.resize(1200, 800)
    page.show()
    qapp.processEvents()
    yield page
    page.hide()
    page.deleteLater()


# -- the speed setting ---------------------------------------------------------------

def test_speed_is_clamped_to_the_slider_range() -> None:
    from fps_coach.dashboard_config import (
        BOUNCE_SPEED_DEFAULT,
        BOUNCE_SPEED_MAX,
        BOUNCE_SPEED_MIN,
        clamp_bounce_speed,
    )

    assert clamp_bounce_speed(1) == BOUNCE_SPEED_MIN
    assert clamp_bounce_speed(10_000) == BOUNCE_SPEED_MAX
    assert clamp_bounce_speed(140) == 140
    assert clamp_bounce_speed("150") == 150  # QSettings may store text
    assert clamp_bounce_speed("fast") == BOUNCE_SPEED_DEFAULT
    assert clamp_bounce_speed(None) == BOUNCE_SPEED_DEFAULT


# -- entering and leaving the mode ---------------------------------------------------

def test_turning_it_on_moves_every_tile_out_of_the_grid(qapp, shown_dashboard) -> None:
    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()

    assert page.grid.count() == 0
    assert len(page._bouncers) == len(page.TILES)
    for tile in page._tiles:
        assert tile.parent() is page.grid_host
        assert tile.isVisible()
        assert not any(button.isVisible() for button in tile.edit_buttons)
    assert page.grid_host.minimumHeight() == page.grid_host.maximumHeight()  # a fixed stage


def test_turning_it_off_puts_the_tiles_back_in_the_grid(qapp, shown_dashboard) -> None:
    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    page.set_serious_mode(False)
    qapp.processEvents()

    assert page.grid.count() == len(page.TILES)
    assert page._bouncers == []
    assert not page._bounce_timer.isActive()
    assert page.grid_host.maximumHeight() > 10_000  # the stage height is released
    for tile in page._tiles:
        assert tile.maximumWidth() > 10_000
        assert all(button.isVisible() for button in tile.edit_buttons)


def test_the_checkbox_and_the_saved_setting_agree(qapp, shown_dashboard, store) -> None:
    page = shown_dashboard
    page.serious_check.setChecked(True)  # what a click does
    qapp.processEvents()
    assert page.serious_mode is True
    assert store.value("dashboard/serious_mode", False, type=bool) is True
    assert len(page._bouncers) == len(page.TILES)

    page.set_serious_mode(False)
    assert page.serious_check.isChecked() is False
    assert store.value("dashboard/serious_mode", True, type=bool) is False


def test_the_mode_survives_a_restart(qapp, store, monkeypatch) -> None:
    from fps_coach.ui.pages_setup import DashboardPage

    store.setValue("dashboard/serious_mode", True)
    page = DashboardPage()
    page.resize(1200, 800)
    page.show()
    qapp.processEvents()
    try:
        assert page.serious_check.isChecked()
        assert len(page._bouncers) == len(page.TILES)
    finally:
        page.hide()
        page.deleteLater()


def test_a_theme_rebuild_keeps_the_tiles_bouncing_without_duplicates(qapp, shown_dashboard) -> None:
    from PySide6.QtWidgets import QFrame

    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    page._rebuild_tiles()  # main window calls this on every theme apply
    qapp.processEvents()

    assert len(page._bouncers) == len(page.TILES)
    shown = [child for child in page.grid_host.findChildren(QFrame)
             if child.objectName() == "tile" and not child.isHidden()]
    assert len(shown) == len(page.TILES)  # old tiles are hidden until Qt deletes them
    assert page.grid.count() == 0


def test_hidden_page_does_not_move_and_starts_when_shown(qapp, store) -> None:
    from fps_coach.ui.pages_setup import DashboardPage

    page = DashboardPage()
    try:
        page.set_serious_mode(True)
        qapp.processEvents()
        assert page._bouncers == []  # not on screen: nothing to lay out
        assert not page._bounce_timer.isActive()

        page.resize(1200, 800)
        page.show()
        qapp.processEvents()
        assert len(page._bouncers) == len(page.TILES)
        assert page._bounce_timer.isActive()

        page.hide()
        qapp.processEvents()
        assert not page._bounce_timer.isActive()
    finally:
        page.deleteLater()


# -- movement -------------------------------------------------------------------------

def test_tiles_stay_inside_the_stage_for_a_long_run(qapp, shown_dashboard, monkeypatch) -> None:
    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    monkeypatch.setattr(page, "_frame_clock", _FixedClock(16))
    page.set_bounce_speed(300)

    width, height = page.grid_host.width(), page.grid_host.height()
    reversed_at_wall = set()
    for _ in range(4000):  # about a minute at 60 frames a second
        before = {id(b): (b.ux, b.uy) for b in page._bouncers}
        page._bounce_step()
        for b in page._bouncers:
            assert 0 <= b.tile.x() <= max(0, width - b.tile.width())
            assert 0 <= b.tile.y() <= max(0, height - b.tile.height())
            if (b.ux, b.uy) != before[id(b)]:
                reversed_at_wall.add(id(b))
    assert len(reversed_at_wall) == len(page.TILES)  # every tile bounced off an edge at least once


def test_one_frame_moves_a_tile_by_speed_times_time(qapp, shown_dashboard, monkeypatch) -> None:
    from fps_coach.dashboard_config import BOUNCE_BASE_SPEED

    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    monkeypatch.setattr(page, "_frame_clock", _FixedClock(50))  # 0.05 s
    page.set_bounce_speed(100)
    bouncer = page._bouncers[4]  # the middle column of the second row: away from the edges
    start_x, start_y = bouncer.x, bouncer.y
    page._bounce_step()
    # A 50 ms frame is the longest step taken as is; longer stalls are capped (see the stall test).
    assert bouncer.x - start_x == pytest.approx(bouncer.ux * BOUNCE_BASE_SPEED * 0.05)
    assert bouncer.y - start_y == pytest.approx(bouncer.uy * BOUNCE_BASE_SPEED * 0.05)


def test_faster_setting_moves_tiles_faster(qapp, shown_dashboard, monkeypatch) -> None:
    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    monkeypatch.setattr(page, "_frame_clock", _FixedClock(16))
    bouncer = page._bouncers[4]

    def travel(percent: int) -> float:
        page.set_bounce_speed(percent)
        bouncer.x, bouncer.y = 400.0, 200.0
        page._bounce_step()
        return abs(bouncer.x - 400.0) + abs(bouncer.y - 200.0)

    slow, fast = travel(50), travel(200)
    assert fast == pytest.approx(slow * 4, rel=1e-6)


def test_a_long_stall_does_not_teleport_a_tile(qapp, shown_dashboard, monkeypatch) -> None:
    from fps_coach.ui.pages_setup import BOUNCE_MAX_STEP_S

    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    monkeypatch.setattr(page, "_frame_clock", _FixedClock(10_000))  # a 10 s stall
    page.set_bounce_speed(100)
    bouncer = page._bouncers[0]
    bouncer.x, bouncer.y = 300.0, 200.0
    page._bounce_step()
    moved = abs(bouncer.x - 300.0) + abs(bouncer.y - 200.0)
    assert moved <= 150.0 * BOUNCE_MAX_STEP_S * 1.5  # Manhattan bound on one capped step


def test_a_tile_is_still_clickable_while_it_moves(qapp, shown_dashboard) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    opened: list[str] = []
    page.navigate.connect(opened.append)
    tile = page._tiles[1]
    QTest.mouseClick(tile, Qt.MouseButton.LeftButton, pos=tile.rect().center())
    assert opened == [tile.key]


def test_reorder_and_hide_are_not_offered_while_moving(qapp, shown_dashboard) -> None:
    page = shown_dashboard
    page.set_serious_mode(True)
    qapp.processEvents()
    assert all(not button.isVisible() for tile in page._tiles for button in tile.edit_buttons)
    page.set_serious_mode(False)
    qapp.processEvents()
    assert all(button.isVisible() for tile in page._tiles for button in tile.edit_buttons)


# -- Settings > Interface -------------------------------------------------------------

# Keeps the Settings pages from being destroyed by Python's garbage collector mid-run: on this
# PySide6 build, deleting a parentless SettingsPage that way prints "shared QObject was deleted
# directly" (the same on the committed HEAD). The app keeps its pages for its whole run.
_KEEP_ALIVE: list = []


def test_settings_speed_slider_saves_and_reports_every_step(qapp, store) -> None:
    from fps_coach.ui.pages_coach import SettingsPage

    page = SettingsPage()
    _KEEP_ALIVE.append(page)
    seen: list[int] = []
    page.bounce_speed_changed.connect(seen.append)
    page.bounce_speed.setValue(180)
    page.bounce_speed.setValue(75)
    assert seen == [180, 75]
    assert store.value("dashboard/bounce_speed", type=int) == 75
    assert page.bounce_speed.value() == 75


def test_settings_speed_readout_shows_the_exact_percentage(qapp, store) -> None:
    from fps_coach.ui.pages_coach import SettingsPage

    page = SettingsPage()
    _KEEP_ALIVE.append(page)
    page.bounce_speed.setValue(137)
    from PySide6.QtWidgets import QLabel

    readouts = [label.text() for label in page.bounce_speed.findChildren(QLabel)]
    assert "137%" in readouts


def test_speed_change_reaches_the_dashboard_through_the_main_window(qapp, store, monkeypatch) -> None:
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()  # uses the temporary store, so the saved settings are not touched
    window.settings_page.bounce_speed.setValue(230)
    assert window.dashboard_page.bounce_speed == 230
