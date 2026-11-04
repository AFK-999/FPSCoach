"""Test Mode is a local comparison, not an OS/game input or Custom Curve emulator."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fps_coach.curve_io import CurvePoint, CurveProfile, save_curve
from fps_coach.test_mode import ComparisonSimulation, neutral_profile, response


def _profile(x_gain=1.0, y_gain=None, name="Curve"):
    y_gain = x_gain if y_gain is None else y_gain
    return CurveProfile(name, [CurvePoint(0, x_gain), CurvePoint(100, x_gain)],
                        [CurvePoint(0, y_gain), CurvePoint(100, y_gain)])


def test_same_movement_produces_two_independent_ghosts_and_scores():
    sim = ComparisonSimulation(_profile(1), _profile(2), 400, 300, 1000)
    sim.move(30, -10, .1)
    assert sim.positions[0] == pytest.approx((230, 140))
    assert sim.positions[1] == pytest.approx((260, 130))
    errors = sim.click(230, 140, 10)
    assert errors == pytest.approx((0, (30**2 + 10**2) ** .5))
    assert [s.hits for s in sim.stats] == [1, 0]
    assert [s.attempts for s in sim.stats] == [1, 1]
    assert sim.stats[1].mean_error == pytest.approx(errors[1])


def test_speed_mapping_and_independent_axes_interpolate_gains():
    profile = CurveProfile("varying", [CurvePoint(0, 1), CurvePoint(100, 3)],
                           [CurvePoint(0, 2), CurvePoint(200, 4)])
    dx, dy, gx, gy = response(profile, 3, 4, .01, 1000)
    assert (gx, gy) == pytest.approx((2, 3))  # 500 px/s -> halfway through each axis
    assert (dx, dy) == pytest.approx((6, 12))
    assert response(profile, 3, 4, .001, 1000)[2:] == pytest.approx((3, 4))


def test_simulation_snapshots_profiles_and_clamps_without_moving_system_cursor():
    a, b = _profile(1), _profile(3)
    sim = ComparisonSimulation(a, b, 100, 100)
    b.horizontal[0].multiplier = 100
    sim.move(100, 100, .01)
    assert sim.positions == [(100, 100), (100, 100)]
    assert sim.profiles[1].horizontal[0].multiplier == 3
    sim.recenter()
    assert sim.positions == [(50, 50), (50, 50)]
    assert sim.stats[0].attempts == 0


def test_invalid_timing_does_not_partially_move_either_profile():
    sim = ComparisonSimulation(neutral_profile(), _profile(2), 100, 100)
    with pytest.raises(ValueError):
        sim.move(10, 0, 0)
    assert sim.positions == [(50, 50), (50, 50)]
    with pytest.raises(ValueError):
        response(neutral_profile(), 1, 1, .01, 0)
    with pytest.raises(ValueError):
        sim.click(float("nan"), 2, 5)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP_ALIVE = []


@pytest.fixture(autouse=True)
def fake_ui_capture(monkeypatch):
    from fps_coach.ui import test_mode as ui

    class Capture:
        def __init__(self, arena, on_move, on_click, on_lost):
            self.on_move, self.on_click, self.on_lost = on_move, on_click, on_lost
            self.active = False
        def start(self):
            self.active = True
        def stop(self):
            self.active = False

    monkeypatch.setattr(ui, "RawMouseCapture", Capture)


def test_dialog_loads_current_and_imported_profiles_and_scores_raw_input(qapp, tmp_path, monkeypatch):
    from fps_coach.ui import test_mode as ui
    from PySide6.QtWidgets import QFileDialog

    current = _profile(1.0, name="Session")
    dialog = ui.TestModeDialog(lambda: current)
    _KEEP_ALIVE.append(dialog)
    dialog.show()
    qapp.processEvents()
    assert "Session" in dialog.slot_labels[0].text()
    imported = _profile(2.0, name="Imported")
    path = save_curve(imported, tmp_path / "double.cc4")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    dialog.import_slot(1)
    assert "Imported" in dialog.slot_labels[1].text()
    dialog.start_comparison()
    assert dialog.arena.active
    ticks = iter((1.0, 1.1))
    monkeypatch.setattr(ui.time, "monotonic", lambda: next(ticks))
    sim = dialog.arena.simulation
    start = deepcopy(sim.positions)
    dialog.arena._capture.on_move(10, 0)  # The first raw packet is not discarded.
    assert sim.positions[0][0] == pytest.approx(start[0][0] + 10)
    assert sim.positions[1][0] == pytest.approx(start[1][0] + 20)
    dialog.arena._capture.on_move(-3, 4)
    assert sim.positions[0][0] == pytest.approx(start[0][0] + 7)
    assert sim.positions[1][0] == pytest.approx(start[1][0] + 14)
    dialog.arena._capture.on_click()
    assert [s.attempts for s in sim.stats] == [1, 1]
    assert "1 hits" in dialog.stats_label.text() or "/1 hits" in dialog.stats_label.text()
    dialog.close()
    assert not dialog.arena.active


def test_profile_or_reference_change_stops_and_clears_comparison(qapp):
    from fps_coach.ui.test_mode import TestModeDialog

    dialog = TestModeDialog(lambda: None)
    _KEEP_ALIVE.append(dialog)
    dialog.start_comparison()
    dialog.set_slot(0, _profile(1.5), "Other")
    assert not dialog.arena.active and dialog.arena.simulation is None
    dialog.start_comparison()
    dialog.speed_reference.setValue(2200)
    assert not dialog.arena.active and dialog.arena.simulation is None
    dialog.use_current(1)
    assert "No current curve" in dialog.stats_label.text()
    dialog.close()


def test_calibration_opens_test_mode_and_disables_it_during_a_session(qapp):
    from fps_coach.backend import _FallbackAnalyzer
    from fps_coach.ui.pages_setup import CalibrationPage

    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP_ALIVE.append(page)
    page.test_mode_btn.click()
    assert page._test_dialog is not None and page._test_dialog.isVisible()
    page._on_session_started()
    assert not page._test_dialog.isVisible()
    assert not page.test_mode_btn.isEnabled()
    page._on_session_cancelled()
    assert page.test_mode_btn.isEnabled()
    page._open_test_mode()
    page._test_dialog.start_comparison()
    page.clear_recommendation_state()
    assert page._test_dialog is None
    page.close()


def test_invalid_profile_import_leaves_both_slots_unchanged(qapp, tmp_path, monkeypatch):
    from fps_coach.ui.test_mode import TestModeDialog
    from PySide6.QtWidgets import QFileDialog

    dialog = TestModeDialog(lambda: None)
    _KEEP_ALIVE.append(dialog)
    invalid = tmp_path / "broken.cc4"
    invalid.write_text("not valid json", encoding="utf-8")
    before = deepcopy(dialog.profiles)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(invalid), ""))
    dialog.import_slot(0)
    assert dialog.profiles == before
    assert "Could not load profile" in dialog.stats_label.text()
    dialog.close()


def test_input_scale_applies_equally_after_curve_gain():
    sim = ComparisonSimulation(_profile(1), _profile(2), 400, 300, 1000, input_scale=.5)
    sim.move(20, -10, .1)
    assert sim.positions[0] == pytest.approx((210, 145))
    assert sim.positions[1] == pytest.approx((220, 140))
    with pytest.raises(ValueError):
        ComparisonSimulation(_profile(), _profile(), 100, 100, input_scale=0)


def test_escape_and_focus_loss_release_capture(qapp):
    from fps_coach.ui.test_mode import TestModeDialog
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtTest import QTest

    dialog = TestModeDialog(lambda: None)
    _KEEP_ALIVE.append(dialog)
    dialog.show()
    qapp.processEvents()
    dialog.start_comparison()
    capture = dialog.arena._capture
    assert capture.active
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not capture.active and not dialog.arena.active
    assert dialog.isVisible()
    dialog.start_comparison()
    assert capture.active
    qapp.sendEvent(dialog, QEvent(QEvent.Type.WindowDeactivate))
    assert not capture.active and not dialog.arena.active
    assert "lost focus" in dialog.stats_label.text()
    dialog.close()


def test_capture_failure_never_starts_an_unconfined_simulation(qapp, monkeypatch):
    from fps_coach.ui import test_mode as ui
    from fps_coach.ui.test_mouse_capture import CaptureError

    class NoCapture:
        def __init__(self, *args):
            pass
        def start(self):
            raise CaptureError("not supported")

    monkeypatch.setattr(ui, "RawMouseCapture", NoCapture)
    dialog = ui.TestModeDialog(lambda: None)
    _KEEP_ALIVE.append(dialog)
    dialog.show()
    qapp.processEvents()
    dialog.start_comparison()
    assert not dialog.arena.active and dialog.arena.simulation is None
    assert "not supported" in dialog.stats_label.text()
    dialog.close()
