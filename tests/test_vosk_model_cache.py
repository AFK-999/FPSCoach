"""Vosk model caching: a model is loaded once and reused, and only one is kept at a time.

vosk and sounddevice are replaced with small fakes, so no real model or microphone is
needed. The fakes count how many times a model is constructed.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from fps_coach import vosk_service


FAKE_DIR = "fake-model-dir"  # one path for every call, so the cache sees the same model


class _FakeModel:
    constructed: list[str] = []

    def __init__(self, path: str) -> None:
        _FakeModel.constructed.append(path)
        self.path = path


class _FakeRecognizer:
    def __init__(self, model, rate) -> None:
        self.model = model

    def AcceptWaveform(self, data) -> bool:  # noqa: N802 - vosk API name
        return True

    def Result(self) -> str:  # noqa: N802
        return json.dumps({"text": "hello coach"})

    def FinalResult(self) -> str:  # noqa: N802
        return json.dumps({"text": ""})


class _FakeStream:
    """Calls the audio callback once on entry, so a listener gets one chunk."""

    def __init__(self, samplerate, blocksize, dtype, channels, callback) -> None:
        self._callback = callback

    def __enter__(self):
        self._callback(b"\x00" * 16000, 8000, None, None)
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def fake_stack(monkeypatch):
    _FakeModel.constructed = []

    vosk = types.ModuleType("vosk")
    vosk.Model = _FakeModel
    vosk.KaldiRecognizer = _FakeRecognizer
    monkeypatch.setitem(sys.modules, "vosk", vosk)

    sd = types.ModuleType("sounddevice")
    sd.RawInputStream = _FakeStream
    monkeypatch.setitem(sys.modules, "sounddevice", sd)

    monkeypatch.setattr(vosk_service, "dependencies_available", lambda: True)
    monkeypatch.setattr(vosk_service, "verify_and_configure", lambda progress=None: FAKE_DIR)
    monkeypatch.setattr(vosk_service, "ensure_model", lambda progress=None: FAKE_DIR)

    vosk_service.release_model()
    yield
    vosk_service.release_model()


def test_same_path_loads_once(fake_stack, tmp_path) -> None:
    first = vosk_service.load_model(tmp_path)
    second = vosk_service.load_model(tmp_path)
    assert first is second
    assert len(_FakeModel.constructed) == 1


def test_other_path_replaces_the_cached_model(fake_stack, tmp_path) -> None:
    a = tmp_path / "small"
    b = tmp_path / "large"
    a.mkdir()
    b.mkdir()
    vosk_service.load_model(a)
    vosk_service.load_model(b)
    assert len(_FakeModel.constructed) == 2
    # Only the new model is held now.
    assert vosk_service._loaded_model is not None
    assert vosk_service._loaded_model[0] == str(b.resolve())


def test_release_drops_the_model_and_next_use_reloads(fake_stack, tmp_path) -> None:
    vosk_service.load_model(tmp_path)
    vosk_service.release_model()
    assert vosk_service._loaded_model is None
    vosk_service.load_model(tmp_path)
    assert len(_FakeModel.constructed) == 2


def test_repeated_one_shot_recognition_reuses_the_model(fake_stack) -> None:
    for _ in range(3):
        ok, text, _message = vosk_service.recognize_once(phrase_limit=2.0)
        assert ok and text == "hello coach"
    assert len(_FakeModel.constructed) == 1


def test_listener_and_one_shot_share_one_model(fake_stack) -> None:
    heard: list[str] = []
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # run one loop iteration, then stop

    vosk_service.listen(FAKE_DIR, heard.append, should_stop)
    ok, text, _ = vosk_service.recognize_once(phrase_limit=2.0)
    assert heard == ["hello coach"]
    assert ok and text == "hello coach"
    assert len(_FakeModel.constructed) == 1
