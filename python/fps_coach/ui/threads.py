"""Background threads and services: Ollama checks, model pulls, chat requests,
voice recognition, voice commands, and text-to-speech. Moved unchanged from the
previous main window so the behavior stays identical."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from fps_coach.ai_chat import AIChatClient, ChatProviderError, ChatSettings, check_ollama, pull_model
from fps_coach.tts_service import speakable_text


class _OllamaStatusCheck(QThread):
    """Probe the local Ollama server off the UI thread (ai_chat.check_ollama)."""

    status_ready = Signal(bool, bool)  # (reachable, has_model)

    def __init__(self, base_url: str, model: str = "", parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._model = model

    def run(self) -> None:
        result = check_ollama(self._base_url, self._model)
        self.status_ready.emit(bool(result["reachable"]), bool(result["has_model"]))


class _ModelPullThread(QThread):
    """Pull an Ollama model off the UI thread (ai_chat.pull_model)."""

    progress = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, base_url: str, model: str, parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._model = model

    def run(self) -> None:
        try:
            pull_model(self._base_url, self._model, progress=lambda msg: self.progress.emit(str(msg)))
        except Exception as exc:  # noqa: BLE001 - report any pull failure to the UI
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit()


class _VoiceListenThread(QThread):
    """Capture one spoken phrase off the UI thread (voice_control.recognize_once)."""

    recognized = Signal(str)
    failed = Signal(str)

    def run(self) -> None:
        from fps_coach.voice_control import recognize_once

        result = recognize_once()
        if result.ok:
            self.recognized.emit(result.text)
        else:
            self.failed.emit(result.message)


class _TtsService(QThread):
    """Background text-to-speech worker. Initializes one engine (with fallbacks, see
    tts_service.init_engine) and speaks queued AI responses off the UI thread."""

    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        import queue

        self._queue: queue.Queue = queue.Queue()
        self._stop = False

    def enqueue(self, text: str) -> None:
        # Clean markdown and symbols here, so the engine never reads them out.
        spoken = speakable_text(text)
        if spoken:
            self._queue.put(spoken)

    def stop(self) -> None:
        self._stop = True
        self._queue.put(None)

    def clear(self) -> int:
        """Drop replies that are queued but not yet spoken. Returns how many were dropped.

        The sentence being read right now is not interrupted: pyttsx3 is not safe to
        call from another thread, so it finishes first.
        """
        import queue

        cleared = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return cleared
            if item is not None:
                cleared += 1

    def run(self) -> None:
        import queue

        from fps_coach import tts_service

        with tts_service.com_apartment():
            tts_service.ensure_dependency(progress=self.status.emit)
            result = tts_service.init_engine(progress=self.status.emit)
            if result.speaker is None:
                self.failed.emit(tts_service.format_unavailable(result))
                return
            self.status.emit("Text-to-speech ready.")
            try:
                while not self._stop:
                    try:
                        text = self._queue.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if text is None:
                        break
                    try:
                        result.speaker.say(text)
                    except Exception:  # noqa: BLE001 - one bad utterance must not kill the worker
                        continue
            finally:
                try:
                    result.speaker.close()
                except Exception:  # noqa: BLE001
                    pass


class _VoiceCommandService(QThread):
    """Background Vosk listener: downloads the model if needed, then streams mic
    audio and emits each finalized phrase (MainWindow parses it as a command)."""

    recognized = Signal(str)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from fps_coach import vosk_service

        try:
            if not vosk_service.ensure_dependencies(progress=self.status.emit):
                self.failed.emit(
                    "Could not auto-install the voice packages (vosk, sounddevice). "
                    "Check your internet connection, or install them manually: "
                    "pip install vosk sounddevice"
                )
                return
            model_path = vosk_service.verify_and_configure(progress=self.status.emit)
            if model_path is None:
                model_path = vosk_service.ensure_model(progress=self.status.emit)
            vosk_service.listen(
                model_path,
                on_text=self.recognized.emit,
                should_stop=lambda: self._stop,
                on_status=self.status.emit,
            )
        except Exception as exc:  # noqa: BLE001 - surface any setup/runtime issue
            self.failed.emit(str(exc))


class _ChatRequest(QThread):
    """Send one conversation turn to the configured AI provider off the UI thread."""

    response_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(self, settings: ChatSettings, messages: list, calibration: str, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._messages = messages
        self._calibration = calibration

    def run(self) -> None:
        try:
            answer = AIChatClient(self._settings).chat(self._messages, curve_context=self._calibration)
        except ChatProviderError as exc:
            self.request_failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the UI alive on any provider error
            self.request_failed.emit(f"Unexpected error: {exc}")
        else:
            self.response_ready.emit(answer)
