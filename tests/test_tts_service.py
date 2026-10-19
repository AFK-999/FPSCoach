import subprocess
import sys
import types

import pytest
from fps_coach import tts_service


class _FakeEngine:
    def __init__(self, voices=("voice",)):
        self._voices = list(voices)
        self.said: list[str] = []

    def getProperty(self, name):
        assert name == "voices"
        return self._voices

    def say(self, text):
        self.said.append(text)

    def runAndWait(self):
        pass

    def stop(self):
        pass


def _install_fake_pyttsx3(monkeypatch, behaviour):
    """behaviour(driverName) -> engine or raises. Installs a fake pyttsx3 module."""
    module = types.ModuleType("pyttsx3")
    module.init = behaviour
    monkeypatch.setitem(sys.modules, "pyttsx3", module)
    monkeypatch.setattr(tts_service, "dependency_available", lambda: True)


# --- existing behaviour ----------------------------------------------------


def test_dependency_available_is_bool() -> None:
    assert isinstance(tts_service.dependency_available(), bool)


def test_ensure_dependency_noop_when_present(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "dependency_available", lambda: True)
    monkeypatch.setattr(tts_service, "missing_platform_packages", lambda platform=None: [])
    called = {"run": False}
    monkeypatch.setattr(
        tts_service.subprocess, "run", lambda *a, **k: called.__setitem__("run", True)
    )
    assert tts_service.ensure_dependency() is True
    assert called["run"] is False  # no pip install attempted


def test_ensure_dependency_installs_then_verifies(monkeypatch) -> None:
    state = {"installed": False}
    monkeypatch.setattr(tts_service, "dependency_available", lambda: state["installed"])
    monkeypatch.setattr(tts_service, "missing_platform_packages", lambda platform=None: [])

    def fake_run(cmd, **kwargs):
        state["installed"] = True
        return None

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    assert tts_service.ensure_dependency() is True


def test_ensure_dependency_false_on_install_failure(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "dependency_available", lambda: False)
    monkeypatch.setattr(tts_service, "missing_platform_packages", lambda platform=None: [])

    def boom(cmd, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(tts_service.subprocess, "run", boom)
    assert tts_service.ensure_dependency() is False


# --- platform dependencies (pywin32 / comtypes) -----------------------------


def test_no_platform_packages_required_on_linux() -> None:
    assert tts_service.missing_platform_packages("linux") == []


def test_missing_windows_packages_are_reported(monkeypatch) -> None:
    monkeypatch.setattr(
        tts_service.importlib.util,
        "find_spec",
        lambda name: None if name == "pythoncom" else object(),
    )
    assert tts_service.missing_platform_packages("win32") == ["pywin32"]


def test_windows_packages_installed_and_rechecked(monkeypatch) -> None:
    installed: set[str] = set()

    def present(module):
        return module in installed or module not in {"pythoncom", "comtypes"}

    monkeypatch.setattr(tts_service, "_module_missing", lambda m: not present(m))
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        installed.update({"pythoncom", "comtypes"})

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    notes: list[str] = []
    still = tts_service.ensure_platform_dependencies(progress=notes.append, platform="win32")
    assert still == []
    assert calls and calls[0][-2:] == ["pywin32", "comtypes"]
    assert any("Installing speech support" in n for n in notes)


def test_platform_install_failure_reports_stderr(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "_module_missing", lambda m: True)

    def fail(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="warning\nERROR: no matching wheel\n")

    monkeypatch.setattr(tts_service.subprocess, "run", fail)
    notes: list[str] = []
    still = tts_service.ensure_platform_dependencies(progress=notes.append, platform="win32")
    assert still == ["pywin32", "comtypes"]
    assert any("no matching wheel" in n for n in notes)


# --- engine fallback chain --------------------------------------------------


def test_default_driver_used_when_it_works(monkeypatch) -> None:
    seen = []

    def init(driverName=None):
        seen.append(driverName)
        return _FakeEngine()

    _install_fake_pyttsx3(monkeypatch, init)
    result = tts_service.init_engine(platform="linux")
    assert result.speaker is not None
    assert result.speaker.name == "pyttsx3 default driver"
    assert result.attempts == []
    assert seen == [None]


def test_falls_back_to_alternate_driver(monkeypatch) -> None:
    def init(driverName=None):
        if driverName is None:
            raise OSError("NSSS init failed")
        return _FakeEngine()

    _install_fake_pyttsx3(monkeypatch, init)
    result = tts_service.init_engine(platform="darwin")
    assert result.speaker is not None
    assert result.speaker.name == "pyttsx3 avspeech driver"
    assert len(result.attempts) == 1
    assert "pyttsx3 default driver" in result.attempts[0]
    assert "NSSS init failed" in result.attempts[0]


def test_engine_without_voices_counts_as_failure(monkeypatch) -> None:
    def init(driverName=None):
        if driverName is None:
            return _FakeEngine(voices=())
        return _FakeEngine()

    _install_fake_pyttsx3(monkeypatch, init)
    result = tts_service.init_engine(platform="darwin")
    assert result.speaker.name == "pyttsx3 avspeech driver"
    assert "no voices" in result.attempts[0]


def test_windows_uses_powershell_when_sapi5_fails(monkeypatch) -> None:
    def init(driverName=None):
        raise RuntimeError("sapi5 COM error")

    _install_fake_pyttsx3(monkeypatch, init)
    monkeypatch.setattr(tts_service.shutil, "which", lambda name: "C:/ps.exe")

    class Probe:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(tts_service.subprocess, "run", lambda *a, **k: Probe())
    result = tts_service.init_engine(platform="win32")
    assert result.speaker is not None
    assert result.speaker.name == "PowerShell System.Speech"
    assert "sapi5 COM error" in result.attempts[0]


def test_windows_powershell_without_voices_reports_reason(monkeypatch) -> None:
    def init(driverName=None):
        raise RuntimeError("driver broken")

    _install_fake_pyttsx3(monkeypatch, init)
    monkeypatch.setattr(tts_service.shutil, "which", lambda name: "C:/ps.exe")

    class Probe:
        returncode = 3
        stderr = ""

    monkeypatch.setattr(tts_service.subprocess, "run", lambda *a, **k: Probe())
    result = tts_service.init_engine(platform="win32")
    assert result.speaker is None
    assert any("no speech voices" in a for a in result.attempts)


def test_everything_fails_gives_clear_message(monkeypatch) -> None:
    def init(driverName=None):
        raise RuntimeError("driver broken")

    _install_fake_pyttsx3(monkeypatch, init)
    monkeypatch.setattr(tts_service.shutil, "which", lambda name: None)
    result = tts_service.init_engine(platform="win32")
    assert result.speaker is None
    message = tts_service.format_unavailable(result, platform="win32")
    assert "unavailable" in message
    assert "driver broken" in message
    assert "PowerShell" in message
    assert "pywin32" in message  # actionable hint


def test_pyttsx3_missing_and_no_windows_fallback(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "dependency_available", lambda: False)
    result = tts_service.init_engine(platform="linux")
    assert result.speaker is None
    assert result.attempts == ["pyttsx3 is not installed"]
    assert "espeak-ng" in tts_service.format_unavailable(result, platform="linux")


def test_progress_reported_between_attempts(monkeypatch) -> None:
    def init(driverName=None):
        raise RuntimeError("boom")

    _install_fake_pyttsx3(monkeypatch, init)
    notes: list[str] = []
    tts_service.init_engine(platform="linux", progress=notes.append)
    # Linux has a single attempt, so no "trying the next option" message.
    assert notes == []


def test_create_engine_removed_in_favour_of_init_engine() -> None:
    assert not hasattr(tts_service, "create_engine")


# --- COM ----------------------------------------------------------------------


def test_com_apartment_is_noop_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with tts_service.com_apartment():
        pass  # must not require pythoncom


def test_com_apartment_uninitializes_on_windows(monkeypatch) -> None:
    events = []
    fake = types.ModuleType("pythoncom")
    fake.CoInitialize = lambda: events.append("init")
    fake.CoUninitialize = lambda: events.append("uninit")
    monkeypatch.setitem(sys.modules, "pythoncom", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ValueError):
        with tts_service.com_apartment():
            raise ValueError("body failed")
    assert events == ["init", "uninit"]
