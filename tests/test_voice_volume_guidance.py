"""Speech volume: the 100% default, and the guidance when it is still too quiet.

Two things are checked here. First, that speech volume is initialised to 100% at start-up
rather than left at a slider's zero minimum or at whatever a half-written settings file
held. Second, that Settings > Voice tells the user where to look when 100% is still not
loud enough, because above 100% is the operating system's decision, not the app's.

No audio is produced: the speech engines are never started.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fps_coach.tts_service import VOLUME_MAX, SpeechOptions  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def pages(qapp):
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
    """An empty, isolated QSettings, as the other page tests use."""
    from fps_coach.ui import pages_coach

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.clear()
    monkeypatch.setattr(pages_coach, "_settings", lambda: store)
    return store


# ---------------------------------------------------------------------------
# The default itself
# ---------------------------------------------------------------------------
def test_the_range_still_runs_from_muted_to_full() -> None:
    # The default is the top of the range; the range itself is unchanged.
    assert VOLUME_MAX == 100


def test_the_library_default_is_full_volume() -> None:
    assert SpeechOptions().volume == VOLUME_MAX
    assert SpeechOptions(volume=250).clamped().volume == VOLUME_MAX


def test_a_fresh_install_stores_and_shows_100_percent(qapp, saved, pages) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    assert saved.value("tts/volume") is None
    page = pages(AIChatPage)
    assert page.volume_slider.value() == 100
    assert page.speech_options().volume == 100
    # Written once, so the setting is explicit rather than implied by a fallback.
    assert saved.value("tts/volume", type=int) == 100


def test_the_start_up_value_does_not_come_from_the_slider_minimum(qapp, saved, pages) -> None:
    """A QSlider starts at the bottom of its range; the voice must not start muted."""
    from fps_coach.ui.pages_coach import AIChatPage

    page = pages(AIChatPage)
    assert page.volume_slider._slider.minimum() == 0
    assert page.volume_slider._slider.value() == 100


def test_a_saved_volume_is_kept(qapp, saved, pages) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    saved.setValue("tts/volume", 55)
    page = pages(AIChatPage)
    assert page.volume_slider.value() == 55
    assert page.speech_options().volume == 55
    assert saved.value("tts/volume", type=int) == 55  # untouched


def test_muted_stays_muted(qapp, saved, pages) -> None:
    """0 is a real choice, not "unset": the default must not overwrite it."""
    from fps_coach.ui.pages_coach import AIChatPage

    saved.setValue("tts/volume", 0)
    page = pages(AIChatPage)
    assert page.volume_slider.value() == 0
    assert saved.value("tts/volume", type=int) == 0


def test_a_corrupt_value_falls_back_to_full_volume(qapp, saved, pages) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    saved.setValue("tts/volume", "not a number")
    page = pages(AIChatPage)
    assert page.volume_slider.value() == 100
    assert saved.value("tts/volume", type=int) == 100


def test_the_default_helper_is_idempotent(saved) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    assert AIChatPage.ensure_volume_default(saved) == 100
    assert AIChatPage.ensure_volume_default(saved) == 100  # second look changes nothing
    saved.setValue("tts/volume", 30)
    assert AIChatPage.ensure_volume_default(saved) == 30
    assert saved.value("tts/volume", type=int) == 30


def test_start_up_also_covers_the_settings_page(qapp, saved, pages) -> None:
    """Opening the AI Coach tab first, then Settings, must not reset anything."""
    from fps_coach.ui.pages_coach import AIChatPage, SettingsPage

    chat = pages(AIChatPage)
    chat.volume_slider.setValue(70)
    pages(SettingsPage)  # builds the Settings page against the same store
    assert saved.value("tts/volume", type=int) == 70
    assert chat.speech_options().volume == 70


# ---------------------------------------------------------------------------
# The Settings > Voice guidance
# ---------------------------------------------------------------------------
def _voice_note(settings_page):
    assert hasattr(settings_page, "volume_note"), "Settings > Voice must carry the volume note"
    return settings_page.volume_note


def test_settings_voice_section_explains_where_to_look(qapp, saved, pages) -> None:
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    note = _voice_note(page).text()
    lowered = note.lower()
    assert "too quiet" in lowered
    # It says the app is already at full volume, so the note cannot read as "turn it up here".
    assert "100%" in note
    # And it names the Windows places to check.
    assert "Sound" in note and "Volume" in note
    assert "Volume mixer" in note


def test_the_note_names_the_communications_ducking_setting(qapp, saved, pages) -> None:
    """Windows quietly turns other audio down when it hears a call; say so."""
    from fps_coach.ui.pages_coach import SettingsPage

    note = _voice_note(pages(SettingsPage)).text().lower()
    assert "communications" in note
    assert "do nothing" in note


def test_the_note_sits_under_the_spoken_responses_switch(qapp, saved, pages) -> None:
    from fps_coach.ui.pages_coach import SettingsPage

    page = pages(SettingsPage)
    note = _voice_note(page)
    card = page.tts_enabled.parentWidget()
    while card is not None and note.parentWidget() is not card:
        card = card.parentWidget()
    assert card is not None, "the note must live in the same card as the speak switch"
    assert page.volume_note.wordWrap() is True  # it is a paragraph, not a one-liner


def test_the_note_points_at_the_in_app_slider_too(qapp, saved, pages) -> None:
    """Someone who did lower the slider should know it exists."""
    from fps_coach.ui.pages_coach import SettingsPage

    tooltip = _voice_note(pages(SettingsPage)).toolTip()
    assert "100%" in tooltip
    assert "AI Coach" in tooltip


def test_the_note_makes_no_claim_about_output_beyond_the_app(qapp, saved, pages) -> None:
    """Honesty check: the app cannot raise Windows' own output, and the note says so."""
    from fps_coach.ui.pages_coach import SettingsPage

    note = _voice_note(pages(SettingsPage)).text().lower()
    for overreach in ("guarantee", "always audible", "above your system", "boost past"):
        assert overreach not in note
