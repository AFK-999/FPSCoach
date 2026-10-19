from fps_coach import voice_control


def test_backend_detection_returns_known_or_none() -> None:
    backend = voice_control.available_backend()
    assert backend in (None, "speech_recognition", "vosk")
    assert voice_control.is_available() == (backend is not None)


def test_recognize_once_without_backend_is_graceful() -> None:
    # In this environment no STT backend is installed -> clear, non-raising result.
    if voice_control.available_backend() is None:
        result = voice_control.recognize_once()
        assert result.ok is False
        assert "install" in result.message.lower() or "package" in result.message.lower()
        assert result.text == ""
