from pathlib import Path

from fps_coach import vosk_service


def test_constants_and_cache_dir() -> None:
    assert vosk_service.DEFAULT_MODEL_NAME in vosk_service.DEFAULT_MODEL_URL
    assert vosk_service.DEFAULT_MODEL_URL.startswith("https://")
    assert isinstance(vosk_service.cache_dir(), Path)
    assert vosk_service.cache_dir().name == ".fpscoach_vosk"


def test_dependencies_available_is_bool() -> None:
    assert isinstance(vosk_service.dependencies_available(), bool)


def test_find_model_none_when_absent() -> None:
    # No model downloaded in the test environment.
    assert vosk_service.find_model() is None


def test_ensure_dependencies_noop_when_present(monkeypatch) -> None:
    monkeypatch.setattr(vosk_service, "dependencies_available", lambda: True)
    called = {"run": False}
    monkeypatch.setattr(vosk_service.subprocess, "run", lambda *a, **k: called.__setitem__("run", True))
    assert vosk_service.ensure_dependencies() is True
    assert called["run"] is False  # no pip install attempted


def test_ensure_dependencies_installs_then_verifies(monkeypatch) -> None:
    state = {"installed": False}
    monkeypatch.setattr(vosk_service, "dependencies_available", lambda: state["installed"])
    monkeypatch.setattr(vosk_service, "missing_packages", lambda: ["vosk", "sounddevice"])

    def fake_run(cmd, **kwargs):
        state["installed"] = True  # simulate a successful pip install
        return None

    monkeypatch.setattr(vosk_service.subprocess, "run", fake_run)
    assert vosk_service.ensure_dependencies() is True


def test_ensure_dependencies_returns_false_on_install_failure(monkeypatch) -> None:
    monkeypatch.setattr(vosk_service, "dependencies_available", lambda: False)
    monkeypatch.setattr(vosk_service, "missing_packages", lambda: ["vosk"])

    def boom(cmd, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(vosk_service.subprocess, "run", boom)
    assert vosk_service.ensure_dependencies() is False


def _make_model(root, name="vosk-model-small-en-us-0.15"):
    model = root / name
    (model / "conf").mkdir(parents=True)
    (model / "am").mkdir()
    (model / "README").write_text("x")
    return model


def test_validate_model(tmp_path) -> None:
    good = _make_model(tmp_path)
    assert vosk_service.validate_model(good) is True
    (tmp_path / "incomplete").mkdir()
    assert vosk_service.validate_model(tmp_path / "incomplete") is False
    assert vosk_service.validate_model(tmp_path / "missing") is False


def test_verify_and_configure_sets_exact_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vosk_service, "cache_dir", lambda: tmp_path)
    model = _make_model(tmp_path)
    # Nothing configured yet -> finds + configures the valid model.
    resolved = vosk_service.verify_and_configure()
    assert resolved == model
    # The exact path was persisted and is returned on the next call.
    assert vosk_service.configured_model_path() == model
    assert (tmp_path / "model_path.txt").read_text().strip().endswith(model.name)


def test_verify_and_configure_fixes_stale_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vosk_service, "cache_dir", lambda: tmp_path)
    # Point the config at a wrong/missing dir, then ensure it self-corrects.
    vosk_service.set_configured_model_path(tmp_path / "does_not_exist")
    assert vosk_service.configured_model_path() is None  # invalid -> ignored
    model = _make_model(tmp_path)
    assert vosk_service.verify_and_configure() == model


def test_verify_and_configure_none_when_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vosk_service, "cache_dir", lambda: tmp_path)
    assert vosk_service.verify_and_configure() is None


def test_listen_without_deps_raises_voskunavailable() -> None:
    if not vosk_service.dependencies_available():
        raised = False
        try:
            vosk_service.listen("nowhere", on_text=lambda _t: None, should_stop=lambda: True)
        except vosk_service.VoskUnavailable:
            raised = True
        assert raised
