from fps_coach import tts_service


def test_dependency_available_is_bool() -> None:
    assert isinstance(tts_service.dependency_available(), bool)


def test_ensure_dependency_noop_when_present(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "dependency_available", lambda: True)
    called = {"run": False}
    monkeypatch.setattr(tts_service.subprocess, "run", lambda *a, **k: called.__setitem__("run", True))
    assert tts_service.ensure_dependency() is True
    assert called["run"] is False  # no pip install attempted


def test_ensure_dependency_installs_then_verifies(monkeypatch) -> None:
    state = {"installed": False}
    monkeypatch.setattr(tts_service, "dependency_available", lambda: state["installed"])

    def fake_run(cmd, **kwargs):
        state["installed"] = True
        return None

    monkeypatch.setattr(tts_service.subprocess, "run", fake_run)
    assert tts_service.ensure_dependency() is True


def test_ensure_dependency_false_on_install_failure(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "dependency_available", lambda: False)

    def boom(cmd, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(tts_service.subprocess, "run", boom)
    assert tts_service.ensure_dependency() is False


def test_create_engine_none_without_dependency(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "dependency_available", lambda: False)
    assert tts_service.create_engine() is None
