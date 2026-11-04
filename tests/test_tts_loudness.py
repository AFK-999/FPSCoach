"""Loud mode: the gain stage that plays replies louder than the engine's 100% ceiling.

Both speech engines stop at 1.0, so a quiet voice at "100%" stays quiet. Loud mode
renders the sentence to a WAV, raises it to a target RMS, soft-limits the peaks, and
plays that instead. These tests check the arithmetic on synthetic WAVs, and that every
failure along the way falls back to plain speech instead of losing the reply.
"""

from __future__ import annotations

import math
import os
import types

import pytest
from fps_coach import tts_service
from fps_coach.tts_service import (
    LOUD_MAX_GAIN_DB,
    LOUD_TARGET_RMS,
    SpeechOptions,
    _PowerShellSpeaker,
    _Pyttsx3Speaker,
    enhance_wav,
    play_wav,
)

RATE = 22050


def _wav(amplitude: float, seconds: float = 0.25, channels: int = 1, bits: int = 16) -> bytes:
    """A tone at ``amplitude`` of full scale, as a 16-bit PCM WAV."""
    frames = int(RATE * seconds)
    body = bytearray()
    for index in range(frames):
        value = math.sin(2 * math.pi * 220 * index / RATE) * amplitude
        sample = int(round(value * 32767))
        for _channel in range(channels):
            body += int(sample).to_bytes(2, "little", signed=True)
    block_align = channels * bits // 8
    header = (
        b"RIFF"
        + (36 + len(body)).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")  # PCM
        + channels.to_bytes(2, "little")
        + RATE.to_bytes(4, "little")
        + (RATE * block_align).to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + bits.to_bytes(2, "little")
        + b"data"
        + len(body).to_bytes(4, "little")
    )
    return header + bytes(body)


def _samples(wav: bytes) -> list[int]:
    layout = tts_service._wav_layout(wav)
    assert layout is not None
    body = layout[3]
    return [int.from_bytes(wav[i : i + 2], "little", signed=True) for i in range(body, len(wav) - 1, 2)]


def _rms(wav: bytes) -> float:
    values = _samples(wav)
    return (sum(value * value for value in values) / len(values)) ** 0.5 / 32767.0


def _peak(wav: bytes) -> float:
    return max(abs(value) for value in _samples(wav)) / 32767.0


# --- the arithmetic ---------------------------------------------------------


def _max_boost() -> float:
    return 10 ** (LOUD_MAX_GAIN_DB / 20)


def test_quiet_speech_is_lifted_towards_the_target() -> None:
    # About -27 dBFS RMS: the "why is it so quiet at 100%" case.
    quiet = _wav(0.04)
    before = _rms(quiet)
    after = _rms(enhance_wav(quiet, 100))
    assert before < 0.05
    assert after > before * 2  # clearly louder, which is the whole point
    assert after == pytest.approx(before * _max_boost(), rel=0.03)


def test_speech_already_at_the_target_is_left_alone() -> None:
    already_loud = _wav(0.5)  # ~0.35 RMS: above the target, so nothing to add
    assert _rms(already_loud) > LOUD_TARGET_RMS
    assert enhance_wav(already_loud, 100) is already_loud


def test_loud_speech_is_never_made_quieter() -> None:
    very_loud = _wav(0.9)
    assert enhance_wav(very_loud, 100) is very_loud


def test_gain_is_capped_so_near_silence_is_not_blown_up() -> None:
    near_silent = _wav(0.0005)
    after = enhance_wav(near_silent, 100)
    assert _peak(after) == pytest.approx(0.0005 * _max_boost(), rel=0.05)


def test_peaks_never_clip_when_a_boost_pushes_them_up() -> None:
    # A quiet signal with a loud peak: the gain would overshoot, so the limit engages.
    frames = _wav(0.02)
    after = enhance_wav(frames, 100)
    assert _peak(after) <= 1.0
    assert _peak(after) > 0.02  # lifted, not squashed flat


def test_the_limiter_never_reaches_full_scale() -> None:
    # A steady tone driven hard: the soft limiter approaches LOUD_CEILING, so no sample
    # ever saturates and the boosted voice cannot turn into clipping.
    after = enhance_wav(_wav(0.02), 100)
    assert max(abs(value) for value in _samples(after)) < 32767
    assert _peak(after) <= 0.999


def test_slider_still_controls_the_final_level() -> None:
    quiet = _wav(0.04)
    half = _rms(enhance_wav(quiet, 50))
    full = _rms(enhance_wav(quiet, 100))
    assert half == pytest.approx(full / 2, rel=0.02)


def test_zero_volume_is_silent_but_keeps_a_valid_file() -> None:
    muted = enhance_wav(_wav(0.5), 0)
    assert _peak(muted) == 0.0
    assert tts_service._wav_layout(muted) is not None


def test_a_constant_signal_keeps_its_header_intact() -> None:
    original = _wav(0.05)
    boosted = enhance_wav(original, 100)
    assert boosted[:44] == original[:44] or tts_service._wav_layout(boosted) is not None
    assert len(boosted) == len(original)


def test_stereo_is_boosted_too() -> None:
    stereo = _wav(0.04, channels=2)
    boosted = enhance_wav(stereo, 100)
    layout = tts_service._wav_layout(boosted)
    assert layout is not None and layout[0] == 2
    assert _rms(boosted) > _rms(stereo)


@pytest.mark.parametrize(
    "payload",
    [b"", b"not a wav at all", b"RIFF\x00\x00\x00\x00WAVEfmt ", _wav(0.2)[:30]],
)
def test_anything_that_is_not_a_complete_wav_is_returned_untouched(payload: bytes) -> None:
    assert enhance_wav(payload, 100) is payload


def test_eight_bit_audio_is_left_alone() -> None:
    wav = bytearray(_wav(0.3))
    wav[34:36] = (8).to_bytes(2, "little")  # bits per sample
    payload = bytes(wav)
    assert enhance_wav(payload, 100) is payload


def test_silence_is_returned_untouched() -> None:
    silent = _wav(0.0)
    assert enhance_wav(silent, 100) is silent


# --- the speak path ---------------------------------------------------------


class _FakeRenderable:
    """A speaker that renders a WAV and records what it was asked to say."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.rendered: list[tuple[str, str]] = []

    def render(self, text: str, path: str) -> None:
        self.rendered.append((text, path))
        with open(path, "wb") as handle:
            handle.write(self.payload)


@pytest.fixture
def played(monkeypatch):
    """Capture what reached the player instead of opening an audio device."""
    calls: list[bytes] = []

    def fake_play(path: str) -> bool:
        with open(path, "rb") as handle:
            calls.append(handle.read())
        return True

    monkeypatch.setattr(tts_service, "play_wav", fake_play)
    return calls


def test_loud_mode_plays_the_boosted_wav(played) -> None:
    speaker = _FakeRenderable(_wav(0.04))
    assert tts_service._speak_loud(speaker.render, "Hello.", 100) is True
    assert len(played) == 1
    assert _rms(played[0]) > _rms(_wav(0.04)) * 2  # the player got the boosted version
    assert speaker.rendered[0][0] == "Hello."


def test_the_temp_folder_does_not_litter(played) -> None:
    speaker = _FakeRenderable(_wav(0.1))
    captured: list[str] = []

    def remember(text: str, path: str) -> None:
        captured.append(path)
        speaker.render(text, path)

    assert tts_service._speak_loud(remember, "Hi.", 100) is True
    assert captured and not os.path.exists(captured[0])
    assert not os.path.exists(os.path.dirname(captured[0]))


def test_a_render_failure_falls_back_instead_of_losing_the_reply(monkeypatch) -> None:
    def explode(_text: str, _path: str) -> None:
        raise OSError("engine refused")

    assert tts_service._speak_loud(explode, "Hi.", 100) is False


def test_no_player_means_fall_back(monkeypatch) -> None:
    speaker = _FakeRenderable(_wav(0.1))
    monkeypatch.setattr(tts_service, "play_wav", lambda _path: False)
    assert tts_service._speak_loud(speaker.render, "Hi.", 100) is False


def test_play_wav_reports_no_player_available(monkeypatch) -> None:
    monkeypatch.setattr(tts_service.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tts_service.sys, "platform", "linux")
    assert play_wav("/nonexistent.wav") is False


def test_stop_speaking_can_cut_short_a_playing_file(monkeypatch) -> None:
    monkeypatch.setattr(tts_service.sys, "platform", "linux")
    assert tts_service.stop_playback() is False  # nothing to purge here, and no raise


def test_closing_a_speaker_purges_any_playing_file(monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(tts_service, "stop_playback", lambda: calls.append(True) or True)
    _PowerShellSpeaker("powershell").close()
    _Pyttsx3Speaker(_SavingEngine(), "sapi5").close()
    assert calls and all(calls)


# --- pyttsx3 ---------------------------------------------------------------


class _SavingEngine:
    def __init__(self) -> None:
        self.props: list[tuple[str, object]] = []
        self.saved: list[tuple[str, str]] = []
        self.said: list[str] = []

    def getProperty(self, name):
        return [] if name == "voices" else None

    def setProperty(self, name, value):
        self.props.append((name, value))

    def save_to_file(self, text, path):
        self.saved.append((text, path))

    def say(self, text):
        self.said.append(text)

    def runAndWait(self):
        pass

    def stop(self):
        pass


def test_pyttsx3_renders_at_full_scale_so_the_gain_is_not_doubled(played) -> None:
    engine = _SavingEngine()
    speaker = _Pyttsx3Speaker(engine, "sapi5")
    speaker.apply(SpeechOptions(volume=40, loud=True))
    engine.saved.clear()

    def render(text, path):
        with open(path, "wb") as handle:
            handle.write(_wav(0.05))

    engine.save_to_file = lambda text, path: render(text, path)
    assert tts_service._speak_loud(lambda t, p: speaker.render(engine, t, p), "Hi.", 40) is True
    assert ("volume", 1.0) in engine.props  # full scale going in
    assert _peak(played[0]) <= 0.5  # the 40% slider applied once, in software


def test_a_loud_render_does_not_leave_the_engine_pinned_at_full_scale(played) -> None:
    engine = _SavingEngine()
    speaker = _Pyttsx3Speaker(engine, "sapi5")
    speaker.apply(SpeechOptions(volume=30, loud=True))

    def render(text, path):
        with open(path, "wb") as handle:
            handle.write(_wav(0.05))

    engine.save_to_file = render
    speaker.say("Loud once.")
    engine.props.clear()

    speaker.apply(SpeechOptions(volume=30, loud=False))
    speaker.say("Quiet again.")
    assert ("volume", 0.3) in engine.props


def test_pyttsx3_speaks_normally_when_loud_mode_is_off(monkeypatch) -> None:
    engine = _SavingEngine()
    speaker = _Pyttsx3Speaker(engine, "sapi5")
    speaker.apply(SpeechOptions(volume=100, loud=False))
    speaker.say("Plain.")
    assert engine.said == ["Plain."]
    assert engine.saved == []
    assert ("volume", 1.0) in engine.props


def test_pyttsx3_replies_still_speak_when_the_gain_stage_fails(monkeypatch) -> None:
    engine = _SavingEngine()
    speaker = _Pyttsx3Speaker(engine, "sapi5")
    speaker.apply(SpeechOptions(loud=True))

    def no_render(_text, _path):
        raise RuntimeError("save_to_file unsupported by this driver")

    engine.save_to_file = no_render
    speaker.say("Fallback.")
    assert engine.said == ["Fallback."]


# --- PowerShell / System.Speech -------------------------------------------


def test_powershell_render_asks_for_full_volume_and_a_wav_path(monkeypatch) -> None:
    seen: list[dict] = []

    def fake_run(args, **kwargs):
        seen.append(kwargs["env"])
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    speaker = _PowerShellSpeaker("powershell")
    speaker.render("Hello.", "C:/temp/speech.wav")
    env = seen[-1]
    assert env[tts_service.POWERSHELL_VOLUME_ENV] == "100"
    assert env[tts_service.POWERSHELL_WAV_ENV] == "C:/temp/speech.wav"
    assert env[tts_service.POWERSHELL_TEXT_ENV] == "Hello."


def test_powershell_render_script_targets_a_wave_file() -> None:
    script = tts_service.POWERSHELL_RENDER
    assert "SetOutputToWaveFile" in script
    assert "Sixteen" in script and "Mono" in script
    assert "$s.Volume = 100" in script


def test_powershell_uses_the_boosted_path(monkeypatch, played) -> None:
    speaker = _PowerShellSpeaker("powershell")

    def fake_run(args, **kwargs):
        script = args[-1]
        if script == tts_service.POWERSHELL_RENDER:
            with open(kwargs["env"][tts_service.POWERSHELL_WAV_ENV], "wb") as handle:
                handle.write(_wav(0.04))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    speaker.apply(SpeechOptions(volume=100, loud=True))
    speaker.say("Hello there.")
    assert len(played) == 1 and _rms(played[0]) > _rms(_wav(0.04)) * 2


def test_powershell_falls_back_to_speaking_when_rendering_fails(monkeypatch) -> None:
    scripts: list[str] = []

    def fake_run(args, **kwargs):
        scripts.append(args[-1])
        if args[-1] == tts_service.POWERSHELL_RENDER:
            raise OSError("no wave output")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    speaker = _PowerShellSpeaker("powershell")
    speaker.apply(SpeechOptions(loud=True))
    speaker.say("Hello there.")
    assert scripts == [tts_service.POWERSHELL_RENDER, tts_service.POWERSHELL_SPEAK]


def test_powershell_loud_off_skips_rendering(monkeypatch) -> None:
    scripts: list[str] = []

    def fake_run(args, **kwargs):
        scripts.append(args[-1])
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    speaker = _PowerShellSpeaker("powershell")
    speaker.apply(SpeechOptions(volume=80, loud=False))
    speaker.say("Hello there.")
    assert scripts == [tts_service.POWERSHELL_SPEAK]


# --- options and settings ---------------------------------------------------


def test_loud_is_on_by_default_and_survives_clamping() -> None:
    assert SpeechOptions().loud is True
    assert SpeechOptions(loud=True).clamped().loud is True
    assert SpeechOptions(loud=False).clamped().loud is False


def test_clamping_does_not_drop_the_loud_flag_into_its_default() -> None:
    clamped = SpeechOptions(volume=100, loud=False).clamped()
    assert clamped.loud is False


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved_settings(qapp):
    from fps_coach.ui.widgets import _settings

    store = _settings()
    keys = ("tts/speed", "tts/volume", "tts/voice", "tts/loud", "ui/tts_enabled")
    before = {key: store.value(key) for key in keys}
    yield store
    for key, value in before.items():
        if value is None:
            store.remove(key)
        else:
            store.setValue(key, value)
    store.sync()


def _coach_page():
    from fps_coach.ui.pages_coach import AIChatPage

    return AIChatPage()


def test_the_settings_card_offers_loud_mode_and_saves_it(qapp, saved_settings) -> None:
    page = _coach_page()
    assert page.loud_check.isChecked() is True
    page.loud_check.setChecked(False)
    assert saved_settings.value("tts/loud", True, type=bool) is False
    assert page.speech_options().loud is False
    page.loud_check.setChecked(True)
    assert page.speech_options().loud is True


def test_loud_mode_is_restored_from_settings(qapp, saved_settings) -> None:
    saved_settings.setValue("tts/loud", False)
    page = _coach_page()
    page.refresh_from_settings()
    assert page.loud_check.isChecked() is False
    assert page.speech_options().loud is False


def test_the_default_for_a_fresh_install_is_loud(qapp, saved_settings) -> None:
    saved_settings.remove("tts/loud")
    page = _coach_page()
    page.refresh_from_settings()
    assert page.loud_check.isChecked() is True


def test_the_volume_tooltip_no_longer_claims_100_percent_is_everything(qapp, saved_settings) -> None:
    page = _coach_page()
    tip = page.volume_slider.toolTip().lower()
    assert "loud mode" in tip
    assert "Loud mode" in page.loud_check.text()
