"""Minimal local/online chat providers using only the Python standard library."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SYSTEM_PROMPT = """You are the FPS Coach assistant. Help the user interpret their in-app
calibration, comfort, practice plan, and manually configured response curve. Be concise and
honest about uncertainty. Never provide target detection, recoil automation, synthetic input,
anti-cheat bypasses, process injection, or other gameplay automation. Do not claim that a curve
will guarantee better performance. Recommend small reversible changes and breaks if discomfort
is mentioned. Calibration data is local and should not be requested unless needed.

Answer the specific question asked. Base recommendations on the calibration summary and curve
data supplied in this conversation, and name the values you used (for example the sensitivity
multiplier or a measured percentage). If that data does not answer the question, say what is
missing instead of guessing. Do not give generic tips when specific data is available. You
cannot see the user's screen or game; say so when a question depends on something you were not
given. Do not invent results, scores, trends, or past sessions.

When the context contains an "In-Game Mode is ON" block, the user has named a game and typed
their own DPI and in-game sensitivity. You may then convert between cm/360 degrees and that
game's in-game sensitivity, and suggest a specific in-game number (for example "try 0.42 in
VALORANT") using those figures, saying it is the user's to apply. Keep such changes small and
reversible, explain the trade-off, and never claim to read, measure, or change the game itself.
In-game mode is session-only and off by default; never ask the user to keep it on while playing."""


VISION_SYSTEM_PROMPT = """A still frame from the user's own capture preview is
attached to the newest message. You may describe what the picture shows about the setup and
the image itself: which screen or mode appears to be open, layout and readability, HUD or
interface clutter, lighting, colour, blur, and whether the picture looks correct. You receive
single still frames, not video, so never claim to be watching continuously or to have seen
what happened between frames.

You must not identify targets, enemies, players, or bodies; must not comment on crosshair or
aim placement; and must not give aim, recoil, movement, or positioning guidance based on what
is on screen. If asked for any of that, decline and offer a setup or comfort observation
instead. Use the attached frame only as context for the coaching topics you already support,
and say plainly when the picture does not show enough to answer."""


class ChatProviderError(RuntimeError):
    pass


#: Model-name fragments that mean "this model accepts images" in Ollama.
VISION_MODEL_HINTS: tuple[str, ...] = (
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen2.5vl",
    "qwen3-vl",
    "qwen3vl",
    "llama3.2-vision",
    "llama4",
    "mistral-small3.1",
    "mistral-small3.2",
    "pixtral",
    "granite3.2-vision",
    "internvl",
    "glm-4v",
    "phi4-multimodal",
    "gemma3",
)

#: OpenAI-compatible model families that accept image parts.
_OPENAI_VISION_HINTS: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "chatgpt-4o",
    "o3",
    "o4",
)


def model_supports_images(model: str, provider: str = "ollama") -> bool:
    """Best-effort answer to "can this model see a JPEG?".

    A heuristic, not a capability query: Ollama does not reliably expose
    modalities for a pulled model, and OpenAI-compatible servers vary. It errs
    towards False so the UI can warn before a request fails with a confusing
    provider error. ``gemma3:1b`` is explicitly excluded — that variant is
    text-only while its larger siblings are multimodal.
    """
    name = (model or "").strip().lower()
    if not name:
        return False
    if provider == "openai":
        return any(name.startswith(prefix) or prefix in name for prefix in _OPENAI_VISION_HINTS)
    if name.startswith("gemma3") and ":1b" in name:
        return False
    return any(hint in name for hint in VISION_MODEL_HINTS)


def encode_image(data: bytes) -> str:
    """Base64 for a provider payload."""
    import base64

    return base64.b64encode(bytes(data)).decode("ascii")


# Official Ollama download page (runtime installer for Windows/macOS/Linux).
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"


@dataclass(slots=True)
class ChatSettings:
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:1.7b"
    api_key: str = ""
    temperature: float = 0.35
    timeout_seconds: int = 120
    # When using the Ollama provider, pull the model automatically if it is not
    # already installed locally. Set to False to require a manual ``ollama pull``.
    auto_pull: bool = True
    # Downloading a model can take a while; allow a longer timeout for the pull.
    pull_timeout_seconds: int = 3600


class AIChatClient:
    def __init__(self, settings: ChatSettings) -> None:
        self.settings = settings

    @staticmethod
    def _validated_base_url(value: str) -> str:
        url = value.strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ChatProviderError("Enter a complete HTTP(S) provider URL.")
        return url

    @staticmethod
    def _bounded_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for message in messages[-20:]:
            role = message.get("role", "user")
            if role not in {"user", "assistant", "system"}:
                continue
            content = str(message.get("content", ""))[:12_000]
            if content.strip():
                result.append({"role": role, "content": content})
        return result

    @staticmethod
    def _ollama_api_base(base_url: str) -> str:
        """Return the Ollama ``/api`` root for a validated base URL."""
        return base_url if base_url.endswith("/api") else base_url + "/api"

    def _get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=max(5, self.settings.timeout_seconds)) as response:
                body = response.read(4_000_000)
        except HTTPError as error:
            detail = error.read(800).decode("utf-8", errors="replace").strip()
            raise ChatProviderError(f"Provider returned HTTP {error.code}: {detail or error.reason}") from error
        except URLError as error:
            raise ChatProviderError(f"Could not reach the provider: {error.reason}") from error
        except TimeoutError as error:
            raise ChatProviderError("The model request timed out.") from error
        try:
            value = json.loads(body)
        except json.JSONDecodeError as error:
            raise ChatProviderError("The provider returned an invalid JSON response.") from error
        if not isinstance(value, dict):
            raise ChatProviderError("The provider returned an unexpected response.")
        return value

    def _ollama_has_model(self, api_base: str, model: str) -> bool:
        """Return True if ``model`` is already installed in the local Ollama."""
        response = self._get(api_base + "/tags", {})
        installed = response.get("models")
        if not isinstance(installed, list):
            return False
        family = model.split(":", 1)[0]
        for item in installed:
            name = str(item.get("name", "")) if isinstance(item, dict) else ""
            if name == model or name.split(":", 1)[0] == family:
                return True
        return False

    def _pull_ollama_model(self, api_base: str, model: str, progress=None) -> None:
        """Download ``model`` into the local Ollama, streaming NDJSON progress.

        ``progress`` is an optional callable invoked with each status string so a
        UI can show download progress. Raises ChatProviderError on failure.
        """
        request = Request(
            api_base + "/pull",
            data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(30, self.settings.pull_timeout_seconds)) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        raise ChatProviderError(f"Could not pull '{model}': {event['error']}")
                    if progress is not None:
                        status = str(event.get("status", "")).strip()
                        if status:
                            progress(status)
        except HTTPError as error:
            detail = error.read(800).decode("utf-8", errors="replace").strip()
            raise ChatProviderError(f"Could not pull '{model}': HTTP {error.code}: {detail or error.reason}") from error
        except URLError as error:
            raise ChatProviderError(f"Could not reach Ollama to pull '{model}': {error.reason}") from error
        except TimeoutError as error:
            raise ChatProviderError(f"Pulling '{model}' timed out.") from error

    def ensure_ollama_model(self, api_base: str, model: str, progress=None) -> None:
        """Guarantee ``model`` is available locally before an Ollama chat request.

        If it is missing and ``settings.auto_pull`` is enabled, download it;
        otherwise raise a ChatProviderError with manual instructions.
        """
        if self._ollama_has_model(api_base, model):
            return
        if not self.settings.auto_pull:
            raise ChatProviderError(
                f"Model '{model}' is not installed. Run:  ollama pull {model}"
            )
        if progress is not None:
            progress(f"Model '{model}' not found locally - downloading...")
        self._pull_ollama_model(api_base, model, progress=progress)
        if not self._ollama_has_model(api_base, model):
            raise ChatProviderError(f"Model '{model}' is still unavailable after pulling.")

    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(5, self.settings.timeout_seconds)) as response:
                body = response.read(4_000_000)
        except HTTPError as error:
            detail = error.read(800).decode("utf-8", errors="replace").strip()
            raise ChatProviderError(f"Provider returned HTTP {error.code}: {detail or error.reason}") from error
        except URLError as error:
            raise ChatProviderError(f"Could not reach the provider: {error.reason}") from error
        except TimeoutError as error:
            raise ChatProviderError("The model request timed out.") from error
        try:
            value = json.loads(body)
        except json.JSONDecodeError as error:
            raise ChatProviderError("The provider returned an invalid JSON response.") from error
        if not isinstance(value, dict):
            raise ChatProviderError("The provider returned an unexpected response.")
        return value

    @staticmethod
    def _attach_to_last_user(conversation: list[dict[str, str]], images: Sequence[bytes]):
        """Index of the message the images belong to, appending a user turn if needed."""
        if not conversation:
            conversation.append({"role": "user", "content": "Look at the attached frame."})
            return len(conversation) - 1
        for index in range(len(conversation) - 1, -1, -1):
            if conversation[index].get("role") == "user":
                return index
        conversation.append({"role": "user", "content": "Look at the attached frame."})
        return len(conversation) - 1

    @classmethod
    def _ollama_messages(cls, conversation: list[dict[str, str]], images: Sequence[bytes]) -> list[dict]:
        """Ollama: base64 strings under ``images`` on the message itself."""
        payload = [dict(message) for message in conversation]
        if not images:
            return payload
        index = cls._attach_to_last_user(payload, images)
        payload[index]["images"] = [encode_image(item) for item in images]
        return payload

    @classmethod
    def _openai_messages(cls, conversation: list[dict[str, str]], images: Sequence[bytes]) -> list[dict]:
        """OpenAI-compatible: ``image_url`` parts with a data URI."""
        payload = [dict(message) for message in conversation]
        if not images:
            return payload
        index = cls._attach_to_last_user(payload, images)
        text = str(payload[index].get("content", ""))
        parts: list[dict] = [{"type": "text", "text": text}]
        for item in images:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64," + encode_image(item)},
                }
            )
        payload[index]["content"] = parts
        return payload

    def chat(
        self,
        messages: list[dict[str, str]],
        curve_context: str = "",
        progress=None,
        images: Sequence[bytes] = (),
    ) -> str:
        """Send one turn. ``images`` are JPEG/PNG bytes attached to the newest user message.

        Ollama takes them as base64 on the message; OpenAI-compatible servers take
        them as ``image_url`` content parts. The two payload shapes are built here
        so callers never think about the provider.
        """
        model = self.settings.model.strip()
        if not model:
            raise ChatProviderError("Choose a model name in AI settings.")
        base_url = self._validated_base_url(self.settings.base_url)
        # The built-in safety prompt is always first and cannot be overridden.
        conversation = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Pre-configured persona / instructions / recommendations from
        # config/ai_profile.toml are injected as an ADDITIONAL system message.
        try:
            from fps_coach.ai_profile import load_profile

            preconfig = load_profile().extra_system_text()
        except Exception:
            preconfig = ""
        if preconfig:
            conversation.append({"role": "system", "content": preconfig})
        if curve_context.strip():
            conversation.append(
                {
                    "role": "system",
                    "content": "Current local calibration summary (treat as unverified context):\n"
                    + curve_context[:4_000],
                }
            )
        attachments = [bytes(item) for item in images if item]
        if attachments:
            # Only meaningful when a model that can see exists; the caller decides
            # whether to warn first. This prompt never replaces SYSTEM_PROMPT.
            conversation.append({"role": "system", "content": VISION_SYSTEM_PROMPT})
        conversation.extend(self._bounded_messages(messages))

        if self.settings.provider == "ollama":
            api_base = self._ollama_api_base(base_url)
            self.ensure_ollama_model(api_base, model, progress=progress)
            endpoint = base_url + ("/chat" if base_url.endswith("/api") else "/api/chat")
            payload_messages = self._ollama_messages(conversation, attachments)
            response = self._post(
                endpoint,
                {
                    "model": model,
                    "messages": payload_messages,
                    "stream": False,
                    "options": {"temperature": max(0.0, min(2.0, self.settings.temperature))},
                },
                {},
            )
            content = response.get("message", {}).get("content")
        elif self.settings.provider == "openai":
            if not self.settings.api_key.strip():
                raise ChatProviderError("Enter an API key. It is kept in memory only.")
            if base_url.endswith("/chat/completions"):
                endpoint = base_url
            elif base_url.endswith("/v1"):
                endpoint = base_url + "/chat/completions"
            else:
                endpoint = base_url + "/v1/chat/completions"
            response = self._post(
                endpoint,
                {
                    "model": model,
                    "messages": self._openai_messages(conversation, attachments),
                    "temperature": max(0.0, min(2.0, self.settings.temperature)),
                    "stream": False,
                },
                {"Authorization": f"Bearer {self.settings.api_key.strip()}"},
            )
            choices = response.get("choices")
            content = choices[0].get("message", {}).get("content") if isinstance(choices, list) and choices else None
        else:
            raise ChatProviderError("Unknown AI provider selection.")

        if not isinstance(content, str) or not content.strip():
            error_message = response.get("error")
            raise ChatProviderError(f"The provider returned no message. {error_message or ''}".strip())
        return content.strip()


# Names worth offering in the model list before anything has been fetched. These are
# suggestions to pull, not a claim that they are installed.
_SUGGESTED_TEXT_MODELS = (
    "qwen3:1.7b",
    "phi4-mini",
    "qwen3:4b",
    "llama3.2:3b",
    "qwen2.5:7b",
    "mistral:7b",
)


def suggested_text_models() -> tuple[str, ...]:
    """Model names to offer when the provider has not been asked yet."""
    return _SUGGESTED_TEXT_MODELS


def _unique_names(names) -> list[str]:
    """Strip, drop blanks, and drop case-insensitive duplicates, keeping the first spelling."""
    seen: dict[str, str] = {}
    for name in names or ():
        text = str(name or "").strip()
        if text:
            seen.setdefault(text.casefold(), text)
    return sorted(seen.values(), key=str.casefold)


def _models_endpoint(base_url: str) -> str:
    """The OpenAI-compatible ``/models`` URL for whatever shape the base URL has."""
    base_url = str(base_url or "").strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url[: -len("/chat/completions")] + "/models"
    if base_url.endswith("/v1"):
        return base_url + "/models"
    return base_url + "/v1/models"


def _names_from_tags(payload: dict) -> list[str]:
    """Model names from an Ollama ``/api/tags`` response."""
    entries = payload.get("models")
    if not isinstance(entries, list):
        return []
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            names.append(entry.get("name") or entry.get("model") or "")
    return _unique_names(names)


def _names_from_models(payload: dict) -> list[str]:
    """Model names from an OpenAI-compatible ``/v1/models`` response (or a tags-like one)."""
    entries = payload.get("data")
    if not isinstance(entries, list):
        return _names_from_tags(payload)
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            names.append(entry.get("id") or entry.get("name") or "")
        elif isinstance(entry, str):
            names.append(entry)
    return _unique_names(names)


def list_models(
    base_url: str,
    provider: str = "ollama",
    api_key: str = "",
    timeout: float = 6.0,
) -> list[str]:
    """Ask the provider which models it has, and return their names sorted.

    Ollama answers ``/api/tags`` with the models installed on this machine; an
    OpenAI-compatible server answers ``/models``. The result is what the Settings
    model list shows, so a name that was typed once can be picked again instead of
    remembered. Raises :class:`ChatProviderError` with a message fit for the UI.
    """
    key = str(provider or "").strip().lower()
    client = AIChatClient(
        ChatSettings(base_url=base_url, model="", timeout_seconds=max(2, int(timeout)))
    )
    base = client._validated_base_url(base_url)
    if key in {"ollama", "local"}:
        payload = client._get(client._ollama_api_base(base) + "/tags", {})
        return _names_from_tags(payload)
    if key in {"openai", "openai-compatible", "online"}:
        headers = {}
        if str(api_key or "").strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        try:
            payload = client._get(_models_endpoint(base), headers)
        except ChatProviderError as error:
            if "HTTP 401" in str(error) or "HTTP 403" in str(error):
                raise ChatProviderError(
                    "This endpoint needs an API key to list its models, and this build "
                    "stores no key. Type the model name instead, or use a provider whose "
                    "model list is open."
                ) from error
            raise
        return _names_from_models(payload)
    raise ChatProviderError("Unknown AI provider selection.")


def check_ollama(base_url: str, model: str, timeout: float = 1.5) -> dict[str, bool]:
    """Probe a local Ollama server. Returns {"reachable": bool, "has_model": bool}.

    Never raises - the UI uses this to decide whether to show a setup button.
    """
    client = AIChatClient(ChatSettings(base_url=base_url, model=model, timeout_seconds=int(timeout)))
    try:
        api_base = client._ollama_api_base(client._validated_base_url(base_url))
    except ChatProviderError:
        return {"reachable": False, "has_model": False}
    try:
        has_model = client._ollama_has_model(api_base, model)
    except ChatProviderError:
        return {"reachable": False, "has_model": False}
    return {"reachable": True, "has_model": has_model}


def pull_model(base_url: str, model: str, progress=None) -> None:
    """Download ``model`` into the local Ollama, reporting status lines through
    the optional ``progress`` callback. Raises ChatProviderError on failure.

    This is the one-click path used by the chat UI's download button.
    """
    client = AIChatClient(ChatSettings(base_url=base_url, model=model))
    api_base = client._ollama_api_base(client._validated_base_url(base_url))
    client._pull_ollama_model(api_base, model, progress=progress)
    if not client._ollama_has_model(api_base, model):
        raise ChatProviderError(f"Model '{model}' is still unavailable after downloading.")


def curve_context(summary: dict[str, Any], recommendation: dict[str, Any]) -> str:
    if not summary:
        return "No completed calibration is available."
    reasons = recommendation.get("reasons") or []
    return "\n".join(
        [
            f"Trials: {int(summary.get('trial_count', 0))}",
            f"Path efficiency: {float(summary.get('path_efficiency', 0)):.3f}",
            f"Overshoot score: {float(summary.get('overshoot_score', 0)):.3f}",
            f"Underreach score: {float(summary.get('underreach_score', 0)):.3f}",
            f"Correction score: {float(summary.get('correction_score', 0)):.3f}",
            f"Tremor score: {float(summary.get('tremor_score', 0)):.3f}",
            f"Recommendation: {recommendation.get('title', 'None')}",
            f"Sensitivity multiplier: {float(recommendation.get('sensitivity_multiplier', 1)):.3f}",
            "Reasons: " + "; ".join(str(reason) for reason in reasons),
        ]
    )
