"""Minimal local/online chat providers using only the Python standard library."""

from __future__ import annotations

import json
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
is mentioned. Calibration data is local and should not be requested unless needed."""


class ChatProviderError(RuntimeError):
    pass


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

    def chat(self, messages: list[dict[str, str]], curve_context: str = "", progress=None) -> str:
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
        conversation.extend(self._bounded_messages(messages))

        if self.settings.provider == "ollama":
            api_base = self._ollama_api_base(base_url)
            self.ensure_ollama_model(api_base, model, progress=progress)
            endpoint = base_url + ("/chat" if base_url.endswith("/api") else "/api/chat")
            response = self._post(
                endpoint,
                {
                    "model": model,
                    "messages": conversation,
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
                    "messages": conversation,
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
