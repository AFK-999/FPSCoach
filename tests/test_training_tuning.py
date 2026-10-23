"""Training speed and difficulty, the Precision drill, and the always-visible capture FPS."""

from __future__ import annotations

import pytest

from fps_coach import scenarios


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved(qapp):
    from fps_coach.ui.widgets import _settings

    store = _settings()
    keys = ("training/speed_pct", "training/difficulty", "capture/fps", "capture/preset")
    before = {key: store.value(key) for key in keys}
    yield store
    for key, value in before.items():
        if value is None:
            store.remove(key)
        else:
            store.setValue(key, value)
    store.sync()


# -- pure tuning -----------------------------------------------------------------

def test_default_tuning_leaves_every_drill_unchanged() -> None:
    for sc in scenarios.list_scenarios():
        assert scenarios.tuned(sc) == sc


def test_difficulty_sets_radius_monotonically_smaller() -> None:
    base = scenarios.get("flick")
    radii = [scenarios.tuned(base, 100, level).target_radius for level in range(1, 6)]
    assert radii == sorted(radii, reverse=True)
    assert radii[2] == base.target_radius  # level 3 is the drill's own size
    assert radii[0] > base.target_radius > radii[4]


def test_radius_never_drops_below_minimum() -> None:
    tiny = scenarios.Scenario("t", "t", "", target_count=1, target_radius=4)
    assert scenarios.tuned(tiny, 100, 5).target_radius >= 4


def test_speed_scales_moving_target_velocity() -> None:
    base = scenarios.get("tracking")
    assert scenarios.tuned(base, 200, 3).speed == pytest.approx(base.speed * 2)
    assert scenarios.tuned(base, 50, 3).speed == pytest.approx(base.speed * 0.5)


def test_speed_shortens_reaction_wait_when_faster() -> None:
    base = scenarios.get("reaction")
    assert scenarios.tuned(base, 200, 3).spawn_delay_ms == base.spawn_delay_ms // 2
    assert scenarios.tuned(base, 50, 3).spawn_delay_ms == base.spawn_delay_ms * 2


def test_speed_does_not_change_static_drills() -> None:
    for key in ("flick", "precision", "precision_steady"):
        base = scenarios.get(key)
        assert scenarios.tuned(base, 200, 3).speed == 0.0
        assert scenarios.tuned(base, 200, 3).spawn_delay_ms == 0


def test_out_of_range_values_are_clamped() -> None:
    base = scenarios.get("tracking")
    assert scenarios.tuned(base, 999, 99) == scenarios.tuned(base, 200, 5)
    assert scenarios.tuned(base, -5, -2) == scenarios.tuned(base, 50, 1)


def test_tuning_keeps_the_drill_key_so_analytics_still_group() -> None:
    base = scenarios.get("precision")
    assert scenarios.tuned(base, 150, 4).key == "precision"


# -- catalogue -------------------------------------------------------------------

def test_precision_steady_is_a_separate_drill_from_micro_aim() -> None:
    steady = scenarios.get("precision_steady")
    micro = scenarios.get("precision")
    assert steady.name == "Precision"
    assert micro.name == "Precision micro-aim"
    assert micro.target_radius < steady.target_radius


def test_all_five_training_modes_are_listed() -> None:
    names = {sc.name for sc in scenarios.list_scenarios()}
    assert {"Tracking", "Reaction time", "Precision", "Precision micro-aim", "Flick practice"} <= names


def test_original_drill_definitions_are_unchanged() -> None:
    flick = scenarios.get("flick")
    assert (flick.target_count, flick.target_radius) == (12, 22)
    reaction = scenarios.get("reaction")
    assert (reaction.target_count, reaction.target_radius, reaction.spawn_delay_ms) == (10, 26, 1200)
    tracking = scenarios.get("tracking")
    assert (tracking.target_count, tracking.target_radius, tracking.speed, tracking.moving) == (8, 20, 190.0, True)


# -- training page ---------------------------------------------------------------

def _training_page(qapp, tmp_path=None):
    from fps_coach.analytics import AnalyticsStore
    from fps_coach.ui.pages_practice import TrainingPage

    return TrainingPage(AnalyticsStore(path=None))


def test_training_page_has_speed_and_difficulty_with_exact_readouts(qapp, saved) -> None:
    page = _training_page(qapp)
    page.speed_slider.setValue(150)
    page.difficulty_slider.setValue(5)
    assert page.speed_slider._readout.text() == "1.50x"
    assert page.difficulty_slider._readout.text() == "Level 5"
    assert saved.value("training/speed_pct", type=int) == 150
    assert saved.value("training/difficulty", type=int) == 5


def test_training_page_restores_saved_tuning(qapp, saved) -> None:
    saved.setValue("training/speed_pct", 75)
    saved.setValue("training/difficulty", 2)
    page = _training_page(qapp)
    assert page.speed_slider.value() == 75
    assert page.difficulty_slider.value() == 2


def test_running_drill_uses_the_tuned_radius(qapp, saved) -> None:
    page = _training_page(qapp)
    page.scenario.setCurrentIndex(page.scenario.findData("flick"))
    page.difficulty_slider.setValue(1)
    page._start()
    try:
        assert page.canvas._radius == scenarios.tuned(scenarios.get("flick"), 100, 1).target_radius
    finally:
        page._stop()


def test_tuning_is_locked_while_a_drill_runs(qapp, saved) -> None:
    page = _training_page(qapp)
    page._start()
    try:
        assert not page.speed_slider.isEnabled()
        assert not page.difficulty_slider.isEnabled()
    finally:
        page._stop()
    assert page.speed_slider.isEnabled()


def test_note_explains_what_the_settings_do(qapp, saved) -> None:
    page = _training_page(qapp)
    page.scenario.setCurrentIndex(page.scenario.findData("tracking"))
    assert "moving at" in page.tuning_note.text()
    page.scenario.setCurrentIndex(page.scenario.findData("flick"))
    assert "speed setting applies to moving and reaction drills" in page.tuning_note.text()


# -- capture FPS -----------------------------------------------------------------

class _FakeBackend:
    native_available = True

    def __init__(self):
        self.started = []

    def outputs(self):
        return [{
            "output_name": "Display 1", "adapter_name": "GPU", "width": 1920, "height": 1080,
            "adapter_index": 0, "output_index": 0,
        }]

    def capture_stats(self):
        return {}

    def latest_frame(self):
        return None

    def start_capture(self, *args):
        self.started.append(args)

    def stop_capture(self):
        pass


def _capture_page(qapp):
    from fps_coach.ui.pages_setup import CapturePage

    return CapturePage(_FakeBackend())


def test_fps_choices_are_30_60_120(qapp, saved) -> None:
    page = _capture_page(qapp)
    assert [page.fps_combo.itemData(i) for i in range(page.fps_combo.count())] == [30, 60, 120]


@pytest.mark.parametrize("preset_value", ["quality", "balanced", "low", "custom"])
def test_fps_row_is_visible_for_every_preset(qapp, saved, preset_value) -> None:
    page = _capture_page(qapp)
    page.preset.setCurrentIndex(page.preset.findData(preset_value))
    assert not page.fps_combo.isHidden()


def test_choosing_a_preset_sets_its_frame_rate(qapp, saved) -> None:
    page = _capture_page(qapp)
    page.preset.setCurrentIndex(page.preset.findData("low"))
    assert page.fps_combo.currentData() == 30
    page.preset.setCurrentIndex(page.preset.findData("quality"))
    assert page.fps_combo.currentData() == 120


def test_user_can_override_the_preset_frame_rate(qapp, saved) -> None:
    page = _capture_page(qapp)
    page.preset.setCurrentIndex(page.preset.findData("low"))  # 30 FPS
    page.fps_combo.setCurrentIndex(page.fps_combo.findData(120))
    assert page.fps_combo.currentData() == 120


def test_start_uses_the_selected_frame_rate(qapp, saved) -> None:
    backend = _FakeBackend()
    from fps_coach.ui.pages_setup import CapturePage

    page = CapturePage(backend)
    page.preset.setCurrentIndex(page.preset.findData("balanced"))
    page.fps_combo.setCurrentIndex(page.fps_combo.findData(30))
    page._start()
    assert backend.started, "start_capture was not called"
    assert backend.started[-1][4] == 30  # the target FPS argument
    page._stop()


def test_saved_frame_rate_survives_a_new_page(qapp, saved) -> None:
    page = _capture_page(qapp)
    page.fps_combo.setCurrentIndex(page.fps_combo.findData(120))
    again = _capture_page(qapp)
    assert again.fps_combo.currentData() == 120


def test_saved_preset_without_saved_frame_rate_uses_its_own_rate(qapp, saved) -> None:
    # Regression: a saved Performance preset with no saved FPS reopened at 60 FPS.
    saved.remove("capture/fps")
    saved.setValue("capture/preset", "low")
    page = _capture_page(qapp)
    assert page.preset.currentData() == "low"
    assert page.fps_combo.currentData() == 30


def test_settings_are_in_a_side_panel_outside_the_drill(qapp, saved) -> None:
    page = _training_page(qapp)
    assert page.settings_panel.isAncestorOf(page.speed_slider)
    assert page.settings_panel.isAncestorOf(page.difficulty_slider)
    assert page.settings_panel.isAncestorOf(page.tuning_note)
    assert not page.drill_view.isAncestorOf(page.speed_slider)
    assert not page.drill_view.isAncestorOf(page.difficulty_slider)
    assert not page.settings_panel.isAncestorOf(page.canvas)


def test_side_panel_is_not_inside_the_analytics_view_either(qapp, saved) -> None:
    page = _training_page(qapp)
    assert not page.analytics_page.isAncestorOf(page.settings_panel)
    page.settings_toggle.setChecked(True)  # open the panel from the gear
    page._show_analytics()
    # The panel is not hidden along with the drill, so it stays beside the analytics.
    assert not page.settings_panel.isHidden()
    assert page.drill_view.isHidden()
    page._show_training()
    assert not page.drill_view.isHidden()


def test_settings_are_collapsed_behind_a_gear_icon(qapp, saved) -> None:
    page = _training_page(qapp)
    assert page.settings_panel.isHidden()  # collapsed by default
    assert page.settings_toggle.isCheckable()
    assert page.settings_toggle.toolTip().startswith("Target settings")
    page.settings_toggle.setChecked(True)
    assert not page.settings_panel.isHidden()
    page.settings_toggle.setChecked(False)
    assert page.settings_panel.isHidden()


def test_gear_tooltip_shows_current_values(qapp, saved) -> None:
    page = _training_page(qapp)
    page.speed_slider.setValue(150)
    page.difficulty_slider.setValue(4)
    assert "speed 1.50x" in page.settings_toggle.toolTip()
    assert "Level 4" in page.settings_toggle.toolTip()


def test_gear_uses_the_drawn_settings_icon(qapp, saved) -> None:
    from PySide6.QtWidgets import QToolButton

    page = _training_page(qapp)
    assert isinstance(page.settings_toggle, QToolButton)
    assert not page.settings_toggle.icon().isNull()
