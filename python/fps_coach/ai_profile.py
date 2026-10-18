"""Load the AI chat pre-configuration (persona, instructions, recommendations).

This reads ``config/ai_profile.toml`` at the project root and exposes it to the
chat layer. The built-in safety prompt in :mod:`fps_coach.ai_chat` is always
applied separately and is never overridden here. Loading uses only the standard
library (``tomllib`` on 3.11+, ``tomli`` as a fallback on 3.10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as _toml  # type: ignore


def config_path() -> Path:
    """Return the path to config/ai_profile.toml at the project root.

    This file lives at ``python/fps_coach/ai_profile.py`` so the project root is
    two directories up.
    """
    return Path(__file__).resolve().parents[2] / "config" / "ai_profile.toml"


@dataclass(slots=True)
class AIProfile:
    persona: str = ""
    instructions: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    default_provider: str = "ollama"
    default_model: str = "qwen3:1.7b"
    default_temperature: float = 0.35

    def extra_system_text(self) -> str:
        """Render persona + instructions + recommendations into one system
        message. Returns an empty string when nothing is configured, so the
        caller can skip adding an empty message."""
        parts: list[str] = []
        if self.persona.strip():
            parts.append(self.persona.strip())
        if self.instructions:
            parts.append(
                "Operating instructions:\n"
                + "\n".join(f"- {item}" for item in self.instructions)
            )
        if self.recommendations:
            parts.append(
                "Coaching recommendations you may draw on when relevant:\n"
                + "\n".join(f"- {item}" for item in self.recommendations)
            )
        return "\n\n".join(parts).strip()


def load_profile(path: str | Path | None = None) -> AIProfile:
    """Load the profile, returning defaults if the file is missing or invalid.

    Never raises: a bad or absent config must not break the chat UI.
    """
    target = Path(path) if path else config_path()
    if not target.exists():
        return AIProfile()
    try:
        data = _toml.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return AIProfile()
    profile = data.get("profile", {}) if isinstance(data, dict) else {}
    defaults = data.get("defaults", {}) if isinstance(data, dict) else {}
    return AIProfile(
        persona=str(profile.get("persona", "")),
        instructions=[str(x) for x in profile.get("instructions", []) if str(x).strip()],
        recommendations=[str(x) for x in profile.get("recommendations", []) if str(x).strip()],
        default_provider=str(defaults.get("provider", "ollama")),
        default_model=str(defaults.get("model", "qwen3:1.7b")),
        default_temperature=float(defaults.get("temperature", 0.35)),
    )
