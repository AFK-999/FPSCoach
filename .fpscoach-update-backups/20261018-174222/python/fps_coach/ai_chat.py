"""Minimal local/online chat providers using only the Python standard library."""

from __future__ import annotations

from dataclasses import dataclass
import json
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

    def chat(self, messages: list[dict[str, str]], curve_context: str = "") -> str:
        model = self.settings.model.strip()
        if not model:
            raise ChatProviderError("Choose a model name in AI settings.")
        base_url = self._validated_base_url(self.settings.base_url)
        conversation = [{"role": "system", "content": SYSTEM_PROMPT}]
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
