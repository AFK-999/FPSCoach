"""Speech controls: speed, volume, and voice, applied live. Pitch is not offered.

The engines are replaced with recording fakes, so no audio is produced. The tests check
the exact rate, volume, and voice values each engine would receive.
"""

from __future__ import annotations

import subprocess
import time
import types

import pytest

from fps_coach import tts_service
from fps_coach.tts_service import (
    SpeechOptions,
    _PowerShellSpeaker,
    _Pyttsx3Speaker,
    powershell_rate,
    pyttsx3_rate,
)


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved_settings(qapp):
    from fps_coach.ui.widgets import _settings

    store = _settings()
    keys = ("tts/speed", "tts/volume", "tts/voice", "ui/tts_enabled")
    before = {key: store.value(key) for key in keys}
    yield store
    for key, value in before.items():
        if value is None:
            store.remove(key)
        else:
            store.setValue(key, value)
    store.sync()


class _RecordingEngine:
    """Stands in for a pyttsx3 engine: records every setProperty call."""

    def __init__(self) -> None:
        self.props: list[tuple[str, object]] = []
        self._voice = "id-default"

    def getProperty(self, name):
        if name == "voices":
            return [
                types.SimpleNamespace(id="id-zira", name="Zira"),
                types.SimpleNamespace(id="id-david", name="David"),
            ]
        if name == "voice":
            return self._voice
        raise KeyError(name)

    def setProperty(self, name, value):
        self.props.append((name, value))
        if name == "voice":
            self._voice = value

    def say(self, text):
        pass

    def runAndWait(self):
        pass

    def stop(self):
        pass


# -- options and mappings ---------------------------------------------------------

def test_options_are_clamped_to_their_ranges() -> None:
    assert SpeechOptions(speed=9, volume=150).clamped() == SpeechOptions(2.0, 100, "")
    assert SpeechOptions(speed=0.1, volume=-5).clamped() == SpeechOptions(0.5, 0, "")
    assert SpeechOptions(speed=1.25, volume=60, voice="Zira").clamped() == SpeechOptions(1.25, 60, "Zira")


def test_pyttsx3_rate_is_words_per_minute_scaled() -> None:
    assert pyttsx3_rate(1.0) == 200
    assert pyttsx3_rate(0.5) == 100
    assert pyttsx3_rate(2.0) == 400


def test_powershell_rate_maps_to_minus_ten_to_ten() -> None:
    assert powershell_rate(1.0) == 0
    assert powershell_rate(0.5) == -5
    assert powershell_rate(2.0) == 10
    assert powershell_rate(0.0) == -10
    assert powershell_rate(9.0) == 10


# -- pyttsx3 engine ----------------------------------------------------------------

def test_pyttsx3_apply_sets_rate_volume_and_voice() -> None:
    engine = _RecordingEngine()
    speaker = _Pyttsx3Speaker(engine, None)
    assert speaker.voices() == ["Zira", "David"]
    speaker.apply(SpeechOptions(speed=1.5, volume=40, voice="Zira"))
    assert engine.props == [("rate", 300), ("volume", 0.4), ("voice", "id-zira")]


def test_unchanged_options_are_not_sent_again() -> None:
    engine = _RecordingEngine()
    speaker = _Pyttsx3Speaker(engine, None)
    speaker.voices()
    options = SpeechOptions(speed=1.5, volume=40, voice="Zira")
    speaker.apply(options)
    count = len(engine.props)
    speaker.apply(options)
    assert len(engine.props) == count


def test_empty_voice_restores_the_engine_default() -> None:
    engine = _RecordingEngine()
    speaker = _Pyttsx3Speaker(engine, None)
    speaker.voices()
    speaker.apply(SpeechOptions(voice="David"))
    speaker.apply(SpeechOptions(voice=""))
    assert engine.props[-1] == ("voice", "id-default")


def test_unknown_voice_name_keeps_the_current_voice() -> None:
    engine = _RecordingEngine()
    speaker = _Pyttsx3Speaker(engine, None)
    speaker.voices()
    speaker.apply(SpeechOptions(voice="Nobody"))
    assert not [name for name, _ in engine.props if name == "voice"]


# -- PowerShell fallback -----------------------------------------------------------

def test_powershell_receives_rate_volume_and_voice_by_environment(monkeypatch) -> None:
    seen = {}

    def fake_run(args, **kwargs):
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    speaker = _PowerShellSpeaker("powershell")
    speaker.apply(SpeechOptions(speed=1.5, volume=40, voice="Microsoft Zira"))
    speaker.say("Hold steady.")
    env = seen["env"]
    assert env[tts_service.POWERSHELL_RATE_ENV] == "5"
    assert env[tts_service.POWERSHELL_VOLUME_ENV] == "40"
    assert env[tts_service.POWERSHELL_VOICE_ENV] == "Microsoft Zira"
    assert env[tts_service.POWERSHELL_TEXT_ENV] == "Hold steady."


def test_powershell_script_never_contains_the_reply_text() -> None:
    # The reply travels through the environment, not the command line.
    assert "Hold steady" not in tts_service.POWERSHELL_SPEAK
    assert tts_service.POWERSHELL_TEXT_ENV in tts_service.POWERSHELL_SPEAK


def test_powershell_voice_list_is_read_line_by_line(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "Microsoft Zira\nMicrosoft David\n\n", "")

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    assert _PowerShellSpeaker("powershell").voices() == ["Microsoft Zira", "Microsoft David"]


def test_powershell_voice_list_failure_gives_empty_list(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        raise OSError("no powershell")

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    assert _PowerShellSpeaker("powershell").voices() == []


# -- speaker thread: a change applies to the next sentence -------------------------

class _EventSpeaker:
    name = "event speaker"

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def voices(self) -> list[str]:
        return ["Zira", "David"]

    def apply(self, options) -> None:
        self.events.append(("apply", options))

    def say(self, text: str) -> None:
        self.events.append(("say", text))

    def close(self) -> None:
        pass


def _wait_until(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_change_applies_to_the_next_sentence(qapp, monkeypatch) -> None:
    from fps_coach.ui.threads import _TtsService

    speaker = _EventSpeaker()
    monkeypatch.setattr(tts_service, "ensure_dependency", lambda progress=None, timeout=600.0: True)
    monkeypatch.setattr(
        tts_service, "init_engine",
        lambda platform=None, progress=None: tts_service.InitResult(speaker=speaker),
    )
    voices: list[list[str]] = []
    service = _TtsService(options=SpeechOptions(speed=1.0, volume=100, voice=""))
    service.voices_ready.connect(voices.append)
    service.start()
    service.enqueue("First sentence.")
    assert _wait_until(lambda: ("say", "First sentence.") in speaker.events)

    service.set_options(SpeechOptions(speed=1.5, volume=60, voice="David"))
    service.enqueue("Second sentence.")
    assert _wait_until(lambda: ("say", "Second sentence.") in speaker.events)
    service.stop()
    assert service.wait(5000)
    qapp.processEvents()

    index = speaker.events.index(("say", "Second sentence."))
    kind, applied = speaker.events[index - 1]
    assert kind == "apply"
    assert applied == SpeechOptions(speed=1.5, volume=60, voice="David")
    assert voices == [["Zira", "David"]]


# -- chat page controls -------------------------------------------------------------

def test_speed_and_volume_save_and_emit(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    emitted: list[SpeechOptions] = []
    page.speech_options_changed.connect(emitted.append)
    page.speed_slider.setValue(150)
    page.volume_slider.setValue(40)
    assert emitted[-1] == SpeechOptions(speed=1.5, volume=40, voice="")
    assert saved_settings.value("tts/speed", type=float) == 1.5
    assert saved_settings.value("tts/volume", type=int) == 40
    assert page.speed_slider._readout.text() == "1.50x"
    assert page.volume_slider._readout.text() == "40%"


def test_voice_choice_saves_and_survives_a_new_page(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page.set_voices(["Zira", "David"])
    emitted: list[SpeechOptions] = []
    page.speech_options_changed.connect(emitted.append)
    page.voice_combo.setCurrentIndex(page.voice_combo.findData("David"))
    assert emitted[-1].voice == "David"
    assert saved_settings.value("tts/voice") == "David"

    again = AIChatPage()
    again.set_voices(["Zira", "David"])
    assert again.voice_combo.currentData() == "David"
    assert again.speech_options().voice == "David"


def test_saved_voice_missing_from_this_system_is_kept_but_not_shown(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    saved_settings.setValue("tts/voice", "Gone")
    page = AIChatPage()
    page.set_voices(["Zira"])
    assert page.voice_combo.currentIndex() == 0  # shows Engine default
    assert page.speech_options().voice == "Gone"  # the engine ignores unknown names


def test_list_arriving_late_does_not_erase_the_voice_choice(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    page.speed_slider.setValue(120)  # a slider moves before the voice list has arrived
    saved_settings.setValue("tts/voice", "David")
    page.refresh_from_settings()
    page.set_voices(["Zira", "David"])
    assert page.speech_options().voice == "David"


def test_test_voice_needs_read_aloud_on(qapp, saved_settings) -> None:
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    requests: list[bool] = []
    page.voice_test_requested.connect(lambda: requests.append(True))
    page.read_aloud.setChecked(False)
    page.test_voice_btn.click()
    assert requests == []
    assert "Turn on Read replies aloud" in page.speech_status.text()

    page.read_aloud.setChecked(True)
    page.test_voice_btn.click()
    assert requests == [True]


def test_pitch_is_stated_as_unavailable(qapp, saved_settings) -> None:
    from PySide6.QtWidgets import QLabel

    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    texts = [label.text() for label in page.findChildren(QLabel)]
    assert any("Pitch is not available" in text for text in texts)
    assert not any(hasattr(page, name) for name in ("pitch_slider", "pitch_spin"))


# -- main window: the chat controls reach the running speaker ------------------------

def test_chat_speed_reaches_the_running_speaker(qapp, monkeypatch, saved_settings) -> None:
    from fps_coach.ui.main_window import MainWindow

    saved_settings.setValue("ui/tts_enabled", True)
    speaker = _EventSpeaker()
    monkeypatch.setattr(tts_service, "ensure_dependency", lambda progress=None, timeout=600.0: True)
    monkeypatch.setattr(
        tts_service, "init_engine",
        lambda platform=None, progress=None: tts_service.InitResult(speaker=speaker),
    )
    window = MainWindow()
    try:
        assert window._tts_service is not None
        window.chat_page.speed_slider.setValue(180)
        assert window._tts_service._options.speed == pytest.approx(1.8)
    finally:
        window.close()


def test_coach_prompt_asks_for_specific_answers_and_keeps_safety_rules() -> None:
    from fps_coach.ai_chat import SYSTEM_PROMPT

    assert "Answer the specific question asked" in SYSTEM_PROMPT
    assert "cannot see the user's screen or game" in SYSTEM_PROMPT
    assert "Do not invent results" in SYSTEM_PROMPT
    # The safety rules stay as they were.
    assert "Never provide target detection" in SYSTEM_PROMPT
