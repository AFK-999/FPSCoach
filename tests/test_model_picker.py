"""The Settings model list: reselect a model by name instead of remembering it.

Settings used to present the model as a bare text field, so a name that was typed once and
then lost had to be recalled from memory. It is an editable dropdown now: the names the
provider reports, the name that is currently saved, and a few suggested names, with typing
still allowed for anything else.

These tests cover the parsing (Ollama ``/api/tags``, OpenAI-compatible ``/models``), the
thread that fetches it, and the card itself. Nothing here touches the network: the one
HTTP test serves its own loopback server, and the UI tests replace the thread.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fps_coach import ai_chat  # noqa: E402
from fps_coach.ai_chat import ChatProviderError, list_models, suggested_text_models  # noqa: E402
from PySide6.QtCore import QSettings, QThread, Signal  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved(monkeypatch, tmp_path):
    """Isolated QSettings for the SettingsPage."""
    from fps_coach.ui import pages_coach

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.clear()
    monkeypatch.setattr(pages_coach, "_settings", lambda: store)
    return store


@pytest.fixture
def pages(qapp):
    made = []

    def _make(factory, *args, **kwargs):
        widget = factory(*args, **kwargs)
        made.append(widget)
        return widget

    yield _make
    for widget in made:
        spinner = getattr(widget, "shutdown", None)
        if callable(spinner):
            spinner()
        widget.close()
        widget.deleteLater()
    qapp.processEvents()


def _wait_until(predicate, seconds: float = 5.0, qapp=None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if qapp is not None:
            qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ---------------------------------------------------------------------------
# Parsing and endpoints
# ---------------------------------------------------------------------------
TAGS_PAYLOAD = {
    "models": [
        {"name": "qwen3:1.7b", "size": 1},
        {"name": "llama3.2:latest", "size": 2},
        {"name": "Qwen3:1.7B", "size": 3},  # duplicate spelling, different case
        {"name": "  ", "size": 4},  # blank
        {"model": "phi4-mini"},  # older servers answer with "model"
        "not-a-dict",
    ]
}


def test_ollama_tag_names_are_cleaned_and_deduplicated() -> None:
    names = ai_chat._names_from_tags(TAGS_PAYLOAD)
    # Sorted case-insensitively; the first spelling of a case-only duplicate wins.
    assert names == ["llama3.2:latest", "phi4-mini", "qwen3:1.7b"]


def test_a_missing_models_key_lists_nothing() -> None:
    assert ai_chat._names_from_tags({}) == []
    assert ai_chat._names_from_tags({"models": "nope"}) == []


def test_openai_model_ids_are_read() -> None:
    payload = {"object": "list", "data": [{"id": "gpt-4o-mini"}, {"id": "local-model"}, "plain-string"]}
    assert ai_chat._names_from_models(payload) == ["gpt-4o-mini", "local-model", "plain-string"]


def test_a_tags_shaped_response_also_works_for_openai() -> None:
    """Some local servers answer /v1/models with the Ollama shape."""
    assert ai_chat._names_from_models(TAGS_PAYLOAD) == ["llama3.2:latest", "phi4-mini", "qwen3:1.7b"]


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://host", "http://host/v1/models"),
        ("http://host/", "http://host/v1/models"),
        ("http://host/v1", "http://host/v1/models"),
        ("http://host/v1/chat/completions", "http://host/v1/models"),
    ],
)
def test_the_openai_models_endpoint_follows_the_base_url_shape(base, expected) -> None:
    assert ai_chat._models_endpoint(base) == expected


def test_suggestions_are_names_not_promises() -> None:
    names = suggested_text_models()
    assert names[0] == "qwen3:1.7b"  # the app's default
    assert "phi4-mini" in names
    assert len(set(names)) == len(names)


def test_a_bad_url_is_reported_without_a_request() -> None:
    with pytest.raises(ChatProviderError):
        list_models("not-a-url", "ollama")


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ChatProviderError, match="Unknown AI provider"):
        list_models("http://127.0.0.1:11434", "some-other-thing")


# ---------------------------------------------------------------------------
# Against a real (loopback) provider
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    payload: dict = {}
    status: int = 200
    seen_paths: list[str] = []
    seen_auth: list[str] = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        type(self).seen_paths.append(self.path)
        type(self).seen_auth.append(self.headers.get("Authorization", ""))
        body = json.dumps(type(self).payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the test output
        pass


@pytest.fixture
def provider_server():
    """A loopback HTTP server standing in for Ollama, so the real urllib path runs."""
    _Handler.payload = {"models": [{"name": "qwen3:1.7b"}, {"name": "gemma3:4b"}]}
    _Handler.status = 200
    _Handler.seen_paths = []
    _Handler.seen_auth = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_ollama_models_are_fetched_over_http(provider_server) -> None:
    base_url, handler = provider_server
    assert list_models(base_url, "ollama") == ["gemma3:4b", "qwen3:1.7b"]
    assert handler.seen_paths[-1] == "/api/tags"


def test_an_openai_shaped_server_is_listed_from_v1_models(provider_server) -> None:
    base_url, handler = provider_server
    handler.payload = {"data": [{"id": "gpt-4o-mini"}]}
    assert list_models(base_url, "openai") == ["gpt-4o-mini"]
    assert handler.seen_paths[-1] == "/v1/models"


def test_an_unauthorised_endpoint_explains_itself(provider_server) -> None:
    base_url, handler = provider_server
    handler.status = 401
    with pytest.raises(ChatProviderError, match="needs an API key"):
        list_models(base_url, "openai")


def test_an_unreachable_provider_raises_a_message_the_ui_can_show() -> None:
    with pytest.raises(ChatProviderError, match="Could not reach the provider"):
        list_models("http://127.0.0.1:9", "ollama", timeout=2)


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------
def test_the_list_thread_reports_names(qapp, monkeypatch) -> None:
    from fps_coach.ui.threads import _ModelListThread

    monkeypatch.setattr(
        "fps_coach.ui.threads.list_models", lambda *a, **k: ["b:1", "a:2"]
    )
    got: list[list[str]] = []
    thread = _ModelListThread("http://127.0.0.1:11434", "ollama")
    thread.models_ready.connect(got.append)
    thread.start()
    assert thread.wait(5000)
    qapp.processEvents()
    assert got == [["b:1", "a:2"]]


def test_the_list_thread_reports_failures(qapp, monkeypatch) -> None:
    from fps_coach.ui.threads import _ModelListThread

    def boom(*_args, **_kwargs):
        raise ChatProviderError("Ollama is not running")

    monkeypatch.setattr("fps_coach.ui.threads.list_models", boom)
    failures: list[str] = []
    thread = _ModelListThread("http://127.0.0.1:11434", "ollama")
    thread.failed.connect(failures.append)
    thread.start()
    assert thread.wait(5000)
    qapp.processEvents()
    assert failures == ["Ollama is not running"]


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------
class _InstantListThread(QThread):
    """Stands in for the real list thread: answers immediately with a canned result."""

    models_ready = Signal(list)
    failed = Signal(str)

    def __init__(self, base_url, provider="ollama", api_key="", parent=None):
        super().__init__(parent)
        self.requested = (base_url, provider)
        self.answer = _InstantListThread.answer
        self.error = _InstantListThread.error
        _InstantListThread.last_requested = self.requested

    answer: list[str] = []
    error: str = ""
    last_requested: tuple[str, str] = ("", "")

    def run(self) -> None:
        if self.error:
            self.failed.emit(self.error)
        else:
            self.models_ready.emit(list(self.answer))


def _settings_page(saved, pages, answer=None, error=""):
    from fps_coach.ui.pages_coach import SettingsPage

    _InstantListThread.answer = list(answer or [])
    _InstantListThread.error = error
    page = pages(SettingsPage)
    page._model_thread = None
    return page


def test_the_model_field_is_an_editable_dropdown(saved, pages) -> None:
    from PySide6.QtWidgets import QComboBox

    page = _settings_page(saved, pages)
    assert isinstance(page.model, QComboBox)
    assert page.model.isEditable() is True  # typing a new name still works


def test_the_saved_model_is_first_and_the_suggestions_follow(saved, pages) -> None:
    saved.setValue("ai/model", "qwen3:1.7b")
    page = _settings_page(saved, pages)
    items = [page.model.itemText(i) for i in range(page.model.count())]
    assert items[0] == "qwen3:1.7b"
    # Everything after the saved name is a suggestion, and each one says so.
    assert [line.split(" (")[0] for line in items[1:]] == list(suggested_text_models())[1:]
    assert all("(suggested, not installed)" in line for line in items[1:])
    assert page.model.currentText() == "qwen3:1.7b"


def test_choosing_an_item_saves_it_without_typing(saved, pages) -> None:
    saved.setValue("ai/model", "")
    page = _settings_page(saved, pages)
    index = page._model_index("phi4-mini")
    assert index > 0
    assert page.model.itemText(index).startswith("phi4-mini")

    page.model.setCurrentIndex(index)
    page.model.textActivated.emit(page.model.itemText(index))  # what a user's pick sends

    assert saved.value("ai/model") == "phi4-mini"
    assert page._model_name() == "phi4-mini"


def test_a_list_from_the_provider_replaces_the_suggestions(saved, pages, monkeypatch, qapp) -> None:
    from fps_coach.ui import pages_coach

    monkeypatch.setattr(pages_coach, "_ModelListThread", _InstantListThread)
    saved.setValue("ai/model", "mistral:7b")  # the name the user has configured
    page = _settings_page(saved, pages, answer=["llama3.2:latest", "qwen3:1.7b"])
    page._list_models()
    # The suggestions are already in the box, so wait for the fetched names specifically.
    assert _wait_until(lambda: page.model.findText("llama3.2:latest") >= 0, qapp=qapp)

    items = [page.model.itemText(i) for i in range(page.model.count())]
    assert items[:3] == ["mistral:7b", "llama3.2:latest", "qwen3:1.7b"]
    assert "Found 2 model(s)" in page.status_label.text()
    # Remembered for next time, so the list is populated before any request.
    assert saved.value(page.MODEL_CACHE_KEY) == ["llama3.2:latest", "qwen3:1.7b"]


def test_the_list_button_asks_the_right_provider(saved, pages, monkeypatch, qapp) -> None:
    from fps_coach.ui import pages_coach

    monkeypatch.setattr(pages_coach, "_ModelListThread", _InstantListThread)
    page = _settings_page(saved, pages, answer=["m:1"])
    page.provider.setCurrentText("openai-compatible")
    page._list_models()
    assert _wait_until(lambda: page.status_label.text().startswith("Found"), qapp=qapp)
    assert _InstantListThread.last_requested[1] == "openai"


def test_the_provider_key_translates_the_combo_label(saved, pages) -> None:
    page = _settings_page(saved, pages)
    page.provider.setCurrentText("ollama")
    assert page._provider_key() == "ollama"
    page.provider.setCurrentText("openai-compatible")
    assert page._provider_key() == "openai"


def test_a_cached_list_survives_a_restart(saved, pages) -> None:
    saved.setValue("ai/model", "qwen3:1.7b")
    saved.setValue("ai/model_list", ["mistral:7b", "gemma3:4b"])
    page = _settings_page(saved, pages)
    items = [page.model.itemText(i) for i in range(page.model.count())]
    # The saved name first, then the remembered provider list in the order it was stored.
    assert items[:3] == ["qwen3:1.7b", "mistral:7b", "gemma3:4b"]


def test_an_accidentally_removed_name_can_be_picked_again(saved, pages) -> None:
    """The point of the whole feature: the field is empty, the list is not."""
    saved.setValue("ai/model_list", ["llama3.2:latest", "gemma3:4b"])
    saved.setValue("ai/model", "")
    page = _settings_page(saved, pages)

    assert page._model_name() == ""  # nothing typed, nothing restored
    items = [page.model.itemText(i) for i in range(page.model.count())]
    assert "llama3.2:latest" in items and "gemma3:4b" in items

    page.model.setCurrentIndex(page.model.findText("llama3.2:latest"))
    page.model.textActivated.emit("llama3.2:latest")
    assert saved.value("ai/model") == "llama3.2:latest"


def test_a_failed_list_says_so_and_keeps_the_known_names(saved, pages, monkeypatch, qapp) -> None:
    from fps_coach.ui import pages_coach

    monkeypatch.setattr(pages_coach, "_ModelListThread", _InstantListThread)
    saved.setValue("ai/model_list", ["gemma3:4b"])
    page = _settings_page(saved, pages, error="Ollama is not reachable at this address.")
    page._list_models()
    assert _wait_until(lambda: "Could not list models" in page.status_label.text(), qapp=qapp)
    assert "gemma3:4b" in [page.model.itemText(i) for i in range(page.model.count())]


def test_an_empty_answer_is_not_reported_as_success(saved, pages, monkeypatch, qapp) -> None:
    from fps_coach.ui import pages_coach

    monkeypatch.setattr(pages_coach, "_ModelListThread", _InstantListThread)
    page = _settings_page(saved, pages, answer=[])
    page._list_models()
    assert _wait_until(lambda: "lists no models" in page.status_label.text(), qapp=qapp)


def test_the_hint_labels_a_suggested_name(saved, pages) -> None:
    saved.setValue("ai/model", "")
    page = _settings_page(saved, pages)
    page.model.setCurrentIndex(page._model_index("phi4-mini"))
    page._update_model_hint()
    assert "suggested name" in page.model_hint.text()


def test_the_hint_says_where_the_saved_name_stands(saved, pages) -> None:
    saved.setValue("ai/model", "qwen3:1.7b")
    saved.setValue("ai/model_list", ["gemma3:4b"])
    page = _settings_page(saved, pages)
    assert "saved name" in page.model_hint.text()
    assert "1 model(s) from the provider" in page.model_hint.text()


def test_listing_twice_while_a_request_runs_does_not_stack_threads(saved, pages, monkeypatch) -> None:
    from fps_coach.ui import pages_coach

    monkeypatch.setattr(pages_coach, "_ModelListThread", _InstantListThread)
    page = _settings_page(saved, pages, answer=["m:1"])
    page._list_models()
    first = page._model_thread
    page._list_models()  # still running: ignored
    assert page._model_thread is first


def test_a_picked_suggestion_saves_the_plain_name(saved, pages) -> None:
    """The label says "not installed", the saved value must not."""
    saved.setValue("ai/model", "")
    page = _settings_page(saved, pages)
    index = page._model_index("llama3.2:3b")
    assert page.model.itemText(index).endswith("(suggested, not installed)")
    page.model.setCurrentIndex(index)
    page.model.textActivated.emit(page.model.itemText(index))
    assert saved.value("ai/model") == "llama3.2:3b"


def test_typing_still_works_for_an_unlisted_model(saved, pages) -> None:
    page = _settings_page(saved, pages)
    page.model.setCurrentText("my-own-model:7b")
    page.model.lineEdit().editingFinished.emit()
    assert saved.value("ai/model") == "my-own-model:7b"
    assert page._model_name() == "my-own-model:7b"


def test_shutdown_is_safe_with_no_threads(saved, pages) -> None:
    page = _settings_page(saved, pages)
    page.shutdown()  # must not raise


def test_the_main_window_closes_with_a_list_running(qapp, monkeypatch, tmp_path) -> None:
    """closeEvent waits for a list request instead of tearing the page out from under it."""
    from fps_coach.ui import pages_coach
    from fps_coach.ui.main_window import MainWindow

    store = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    store.clear()
    monkeypatch.setattr(pages_coach, "_settings", lambda: store)
    monkeypatch.setattr(pages_coach, "_ModelListThread", _InstantListThread)
    window = MainWindow()
    try:
        window.settings_page._list_models()
        window.close()
    finally:
        window.deleteLater()
        qapp.processEvents()
