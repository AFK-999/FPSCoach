from fps_coach import ollama_service


def test_non_local_endpoint_is_never_autostarted() -> None:
    # Remote endpoints must never trigger a local 'ollama serve'.
    assert ollama_service.ensure_running("https://api.openai.com") is False


def test_is_running_false_on_dead_port() -> None:
    # Nothing is listening on this port in the test environment.
    assert ollama_service.is_running("http://127.0.0.1:1", timeout=0.5) is False


def test_is_local_detection() -> None:
    assert ollama_service._is_local("http://127.0.0.1:11434") is True
    assert ollama_service._is_local("http://localhost:11434") is True
    assert ollama_service._is_local("https://example.com") is False


def test_find_executable_returns_pair_or_none() -> None:
    result = ollama_service.find_executable()
    assert result is None or (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], str)
        and isinstance(result[1], dict)
    )
