"""In-Game Mode: game catalog, sensitivity maths, settings card, and coach wiring.

Offscreen Qt; no display, no game, and no network are involved. The mode is
session-only, so several tests check that it is *not* restored on launch.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fps_coach.ingame import (  # noqa: E402
    GAMES,
    InGameMode,
    cm_per_360,
    counts_per_360,
    effective_yaw,
    find_game,
    sensitivity_for_cm_per_360,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "python" / "fps_coach" / "ingame.py"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def pages(qapp):
    """Create pages and dispose of them deterministically (no teardown warnings)."""
    made = []

    def _make(factory, *args, **kwargs):
        widget = factory(*args, **kwargs)
        made.append(widget)
        return widget

    yield _make
    for widget in made:
        widget.close()
        widget.deleteLater()
    qapp.processEvents()


@pytest.fixture
def saved(monkeypatch, tmp_path):
    """Isolated QSettings for the SettingsPage, as the other page tests do."""
    from fps_coach.ui import pages_coach
    from PySide6.QtCore import QSettings

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.clear()
    monkeypatch.setattr(pages_coach, "_settings", lambda: store)
    return store


# ---------------------------------------------------------------------------
# Catalog and maths
# ---------------------------------------------------------------------------
def test_catalog_keys_are_unique_and_valid():
    keys = [game.key for game in GAMES]
    assert len(keys) == len(set(keys))
    assert all(game.yaw > 0 for game in GAMES)
    assert all(game.name for game in GAMES)
    assert find_game("does-not-exist").key == GAMES[-1].key
    assert find_game("valorant").name == "VALORANT"


def test_cm_per_360_uses_the_standard_conversion():
    # VALORANT yaw 0.07 deg/count, 0.4 sens, 800 DPI -> 360/(0.07*0.4)=12857 counts
    # -> 12857/800 inch = 16.07 in = 40.8 cm.
    valorant = find_game("valorant")
    assert counts_per_360(valorant.yaw, 0.4) == pytest.approx(12857.14, rel=1e-4)
    assert cm_per_360(valorant.yaw, 800, 0.4) == pytest.approx(40.82, abs=0.02)


def test_sensitivity_round_trips_through_cm_per_360():
    game = find_game("cs2")
    for sens in (0.5, 1.0, 2.4):
        distance = cm_per_360(game.yaw, 1600, sens)
        assert sensitivity_for_cm_per_360(game.yaw, 1600, distance) == pytest.approx(sens)


def test_measured_distance_overrides_the_catalogue_constant():
    game = find_game("other")
    measured = 35.0
    yaw = effective_yaw(game, 800, 1.0, measured)
    assert yaw != pytest.approx(game.yaw)
    # The measurement is now reproduced exactly.
    assert cm_per_360(yaw, 800, 1.0) == pytest.approx(measured)


def test_catalogue_constant_is_used_without_a_measurement():
    game = find_game("valorant")
    assert effective_yaw(game, 800, 0.4, 0.0) == game.yaw


@pytest.mark.parametrize(
    "call",
    [
        lambda: counts_per_360(0.0, 1.0),
        lambda: counts_per_360(0.02, 0.0),
        lambda: cm_per_360(0.02, 0.0, 1.0),
        lambda: sensitivity_for_cm_per_360(0.02, 800, 0.0),
    ],
)
def test_invalid_inputs_raise(call):
    with pytest.raises(ValueError):
        call()


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def test_mode_is_off_when_constructed():
    assert InGameMode().enabled is False
    assert InGameMode(game_key="valorant", dpi=800, sensitivity=0.4).enabled is False


def test_enable_and_disable():
    mode = InGameMode()
    mode.enable()
    assert mode.enabled is True
    mode.disable()
    assert mode.enabled is False


def test_context_block_is_empty_until_enabled():
    mode = InGameMode("valorant", 800, 0.4)
    assert mode.context_block(1.06) == ""
    mode.enable()
    block = mode.context_block(1.06)
    assert block.startswith("In-Game Mode is ON")
    assert "VALORANT" in block
    assert "0.400" in block and "40.8" in block
    assert "0.424" in block  # suggested in-game value
    assert "cannot see the game" in block


def test_feedback_and_suggestion_numbers():
    mode = InGameMode("cs2", 1600, 1.0)
    assert mode.suggested_sensitivity(0.95) == pytest.approx(0.95)
    assert mode.suggestion_cm_per_360(0.95) == pytest.approx(mode.cm_per_360 / 0.95)
    text = mode.feedback(0.95)
    assert "Counter-Strike 2" in text and "cm / 360" in text and "0.95" in text
    assert "cm / 360" in mode.feedback()  # no multiplier still reports the conversion


def test_question_names_the_game_and_the_change():
    mode = InGameMode("valorant", 800, 0.4)
    plain = mode.question()
    assert "VALORANT" in plain and "0.400" in plain and "800 DPI" in plain
    with_change = mode.question(1.06)
    assert "0.424" in with_change and "1.06x" in with_change


def test_setters_validate():
    mode = InGameMode()
    with pytest.raises(ValueError):
        mode.set_dpi(0)
    with pytest.raises(ValueError):
        mode.set_sensitivity(-1)
    with pytest.raises(ValueError):
        mode.set_measured_cm360(-0.5)
    mode.set_measured_cm360(0)  # 0 means "not measured"
    assert mode.measured_cm360 == 0


# ---------------------------------------------------------------------------
# Settings card
# ---------------------------------------------------------------------------
def test_mode_is_off_at_launch_even_if_settings_say_enabled(qapp, saved, pages):
    saved.setValue("ingame/enabled", True)  # pretend something persisted it
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    assert page.ingame_enabled.isChecked() is False
    assert page.ingame_model().enabled is False
    assert page.ingame_context() == ""


def test_the_enabled_flag_is_never_persisted(qapp, saved, pages):
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    page.ingame_enabled.setChecked(True)
    page.ingame_enabled.setChecked(False)
    saved.sync()
    assert not saved.contains("ingame/enabled"), "In-Game Mode must not survive a restart"


def test_controls_are_disabled_until_enabled(qapp, saved, pages):
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    for widget in (page.ingame_game, page.ingame_dpi, page.ingame_sens,
                   page.ingame_measured, page.ingame_ask_btn):
        assert not widget.isEnabled()
    page.ingame_enabled.setChecked(True)
    for widget in (page.ingame_game, page.ingame_dpi, page.ingame_sens,
                   page.ingame_measured, page.ingame_ask_btn):
        assert widget.isEnabled()
    page.ingame_enabled.setChecked(False)
    assert not page.ingame_ask_btn.isEnabled()


def test_selection_and_values_persist(qapp, saved, pages):
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    page.ingame_game.setCurrentIndex(page.ingame_game.findData("valorant"))
    page.ingame_dpi.setValue(1600)
    page.ingame_sens.setValue(0.35)
    page.ingame_measured.setValue(38.5)
    saved.sync()

    assert saved.value("ingame/game") == "valorant"
    assert float(saved.value("ingame/dpi")) == 1600
    assert float(saved.value("ingame/sensitivity")) == pytest.approx(0.35)
    assert float(saved.value("ingame/measured_cm360")) == pytest.approx(38.5)
    assert page.ingame_model().game.key == "valorant"


def test_card_shows_the_conversion_and_the_recommendation(qapp, saved, pages):
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    page.ingame_game.setCurrentIndex(page.ingame_game.findData("valorant"))
    page.ingame_dpi.setValue(800)
    page.ingame_sens.setValue(0.4)
    page.ingame_enabled.setChecked(True)

    assert "Off." in pages(SettingsPage).ingame_result.text()  # a fresh page starts off
    assert "40.8" in page.ingame_result.text()

    page.set_recommendation({"sensitivity_multiplier": 1.06})
    assert "1.06" in page.ingame_result.text()
    assert "0.424" in page.ingame_result.text()
    assert "counts per 360" in page.ingame_stats.text()


def test_measured_distance_is_used_and_named(qapp, saved, pages):
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    page.ingame_dpi.setValue(800)
    page.ingame_sens.setValue(0.4)
    page.ingame_measured.setValue(38.5)  # the user's own measurement wins
    page.ingame_enabled.setChecked(True)
    assert "38.5" in page.ingame_result.text()
    assert "measured" in page.ingame_stats.text().lower()


def test_ask_the_coach_emits_a_prepared_question(qapp, saved, pages):
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    questions: list[str] = []
    page.ingame_question.connect(questions.append)

    page.ingame_ask_btn.click()  # disabled while off: nothing happens
    assert questions == []

    page.ingame_game.setCurrentIndex(page.ingame_game.findData("valorant"))
    page.ingame_sens.setValue(0.4)
    page.ingame_enabled.setChecked(True)
    page.ingame_ask_btn.click()
    assert len(questions) == 1
    assert "VALORANT" in questions[0] and "0.400" in questions[0]


def test_context_block_reaches_the_chat_page(qapp, saved, pages):
    from fps_coach.ui.pages_coach import AIChatPage, SettingsPage

    settings = pages(SettingsPage)
    chat = pages(AIChatPage)
    settings.ingame_changed.connect(lambda: chat.set_ingame_context(settings.ingame_context()))

    settings.ingame_game.setCurrentIndex(settings.ingame_game.findData("valorant"))
    settings.ingame_sens.setValue(0.4)
    settings.ingame_enabled.setChecked(True)
    assert "In-Game Mode is ON" in chat._calibration_context
    assert "VALORANT" in chat._calibration_context

    settings.ingame_enabled.setChecked(False)
    assert "In-Game Mode is ON" not in chat._calibration_context


def test_system_prompt_tells_the_model_what_the_block_means():
    from fps_coach.ai_chat import SYSTEM_PROMPT

    assert "In-Game Mode is ON" in SYSTEM_PROMPT
    assert "session-only" in SYSTEM_PROMPT
    assert "never claim to read" in SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# MainWindow wiring
# ---------------------------------------------------------------------------
@pytest.fixture
def window(qapp, saved):
    from fps_coach.backend import CoreBackend
    from fps_coach.ui.main_window import MainWindow

    win = MainWindow(CoreBackend())
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_main_window_pushes_the_block_to_the_coach(window, qapp):
    page = window.settings_page
    page.ingame_enabled.setChecked(True)
    qapp.processEvents()
    assert "In-Game Mode is ON" in window.chat_page._calibration_context

    page.ingame_enabled.setChecked(False)
    qapp.processEvents()
    assert window.chat_page._calibration_context == "" or (
        "In-Game Mode is ON" not in window.chat_page._calibration_context
    )


def test_ask_the_coach_prefills_the_chat_and_switches_page(window, qapp):
    page = window.settings_page
    page.ingame_game.setCurrentIndex(page.ingame_game.findData("valorant"))
    page.ingame_sens.setValue(0.4)
    page.ingame_enabled.setChecked(True)
    page.ingame_ask_btn.click()
    qapp.processEvents()

    assert window.stack.currentWidget() is window.chat_page
    assert "VALORANT" in window.chat_page.input.text()


def test_calibration_result_updates_the_suggestion(window, monkeypatch, qapp):
    received: list[dict] = []
    monkeypatch.setattr(
        window.settings_page, "set_recommendation", lambda rec: received.append(dict(rec))
    )
    monkeypatch.setattr(window.recommendation_page, "update_result", lambda *a, **k: None)
    monkeypatch.setattr(window.chat_page, "notify_new_calibration", lambda *a, **k: None)

    window._on_calibration_result({"trial_count": 12}, {"sensitivity_multiplier": 0.95})
    qapp.processEvents()
    assert received and received[0]["sensitivity_multiplier"] == 0.95


def test_mode_leaves_no_trace_and_keeps_its_hands_off_the_game(window, qapp, saved):
    """The mode is arithmetic over typed values: no I/O, no game access, no persistence."""
    page = window.settings_page
    page.ingame_enabled.setChecked(True)
    qapp.processEvents()
    saved.sync()
    assert not saved.contains("ingame/enabled")

    # Inspect the module's AST rather than its prose: only stdlib `dataclasses`
    # may be imported, and nothing may open a file, spawn a process, or touch a
    # game/window API.
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"dataclasses", "__future__", "typing"}, imported

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"open", "exec", "eval", "compile", "__import__"}, called

    attribute_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    # No window handles, input injection, or process launching.
    assert not attribute_calls & {
        "ShellExecute",
        "keybd_event",
        "mouse_event",
        "SendInput",
        "Popen",
        "run",
        "system",
    }, attribute_calls
