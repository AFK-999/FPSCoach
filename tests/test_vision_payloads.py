"""Images on the wire: Ollama base64 and OpenAI ``image_url`` parts (rev79).

``AIChatClient.chat`` builds a different payload for each provider. A wrong shape
does not raise — the server silently ignores the picture and the coach answers
about a frame it never saw. These tests assert the exact request bodies.
"""

from __future__ import annotations

import base64
import json

import pytest
from fps_coach.ai_chat import (
    SYSTEM_PROMPT,
    VISION_SYSTEM_PROMPT,
    AIChatClient,
    ChatProviderError,
    ChatSettings,
    encode_image,
    model_supports_images,
)

JPEG = b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES\xff\xd9"


class Recorder:
    """Captures the payload instead of talking to a server."""

    def __init__(self, reply="ok", provider="ollama"):
        self.reply = reply
        self.provider = provider
        self.payload = None
        self.url = None

    def __call__(self, url, payload, headers):
        self.url = url
        self.payload = payload
        if self.provider == "ollama":
            return {"message": {"content": self.reply}}
        return {"choices": [{"message": {"content": self.reply}}]}


def client(monkeypatch, *, provider="ollama", model="gemma3:4b", reply="ok"):
    settings = ChatSettings(provider=provider, model=model, api_key="sk-test")
    chat = AIChatClient(settings)
    recorder = Recorder(reply, provider)
    monkeypatch.setattr(chat, "_post", recorder)
    monkeypatch.setattr(chat, "ensure_ollama_model", lambda *a, **k: None)
    return chat, recorder


# --- capability detection --------------------------------------------------


@pytest.mark.parametrize(
    "model",
    ["gemma3:4b", "qwen2.5vl:3b", "llava:7b", "moondream", "minicpm-v", "llama3.2-vision:11b"],
)
def test_vision_models_are_recognised(model):
    assert model_supports_images(model) is True


@pytest.mark.parametrize("model", ["qwen3:1.7b", "phi4-mini", "gemma3:1b", "llama3:8b", ""])
def test_text_models_are_not_claimed_to_see(model):
    assert model_supports_images(model) is False


def test_openai_vision_families():
    assert model_supports_images("gpt-4o-mini", "openai") is True
    assert model_supports_images("gpt-4.1", "openai") is True
    assert model_supports_images("some-unknown-model", "openai") is False


def test_encode_image_is_plain_base64():
    assert base64.b64decode(encode_image(JPEG)) == JPEG


# --- Ollama ----------------------------------------------------------------


def test_ollama_payload_carries_the_image_as_base64(monkeypatch):
    chat, recorder = client(monkeypatch)
    answer = chat.chat([{"role": "user", "content": "what do you see?"}], images=[JPEG])
    assert answer == "ok"
    messages = recorder.payload["messages"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "what do you see?"
    assert messages[-1]["images"] == [encode_image(JPEG)]
    # The safety prompt is still first, and the vision prompt is added, not swapped in.
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert any(message["content"] == VISION_SYSTEM_PROMPT for message in messages)


def test_ollama_without_images_has_no_images_key(monkeypatch):
    chat, recorder = client(monkeypatch)
    chat.chat([{"role": "user", "content": "hi"}])
    assert all("images" not in message for message in recorder.payload["messages"])
    assert all(message["content"] != VISION_SYSTEM_PROMPT for message in recorder.payload["messages"])


# --- OpenAI ----------------------------------------------------------------


def test_openai_payload_uses_image_url_parts(monkeypatch):
    chat, recorder = client(monkeypatch, provider="openai", model="gpt-4o-mini")
    chat.chat([{"role": "user", "content": "what do you see?"}], images=[JPEG])
    content = recorder.payload["messages"][-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "what do you see?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64," + encode_image(JPEG)
    # JSON-serialisable: the real request goes through json.dumps.
    json.dumps(recorder.payload)


def test_openai_without_images_stays_a_string(monkeypatch):
    chat, recorder = client(monkeypatch, provider="openai", model="gpt-4o-mini")
    chat.chat([{"role": "user", "content": "hi"}])
    assert isinstance(recorder.payload["messages"][-1]["content"], str)


# --- edge cases ------------------------------------------------------------


def test_images_attach_to_the_last_user_turn_when_the_tail_is_a_system_note(monkeypatch):
    chat, recorder = client(monkeypatch)
    chat.chat(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "system", "content": "note"},
        ],
        images=[JPEG],
    )
    messages = recorder.payload["messages"]
    attached = [message for message in messages if message.get("images")]
    assert len(attached) == 1
    assert attached[0]["content"] == "first", "the newest *user* turn takes the image"


def test_images_with_no_user_turn_at_all_get_one(monkeypatch):
    chat, recorder = client(monkeypatch)
    chat.chat([{"role": "assistant", "content": "hello"}], images=[JPEG])
    messages = recorder.payload["messages"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["images"]


def test_empty_image_bytes_are_ignored(monkeypatch):
    chat, recorder = client(monkeypatch)
    chat.chat([{"role": "user", "content": "hi"}], images=[b""])
    assert all("images" not in message for message in recorder.payload["messages"])


def test_provider_errors_still_raise_with_images(monkeypatch):
    chat = AIChatClient(ChatSettings(model="gemma3:4b"))
    monkeypatch.setattr(chat, "_post", lambda *a, **k: {"error": "model requires a newer ollama"})
    monkeypatch.setattr(chat, "ensure_ollama_model", lambda *a, **k: None)
    with pytest.raises(ChatProviderError):
        chat.chat([{"role": "user", "content": "hi"}], images=[JPEG])
