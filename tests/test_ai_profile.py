from pathlib import Path

from fps_coach.ai_profile import AIProfile, load_profile


def test_default_profile_loads_from_repo_config() -> None:
    profile = load_profile()
    assert isinstance(profile, AIProfile)
    # The shipped config has a persona plus instructions/recommendations.
    assert profile.persona.strip()
    assert profile.instructions
    assert profile.recommendations
    text = profile.extra_system_text()
    assert "Operating instructions:" in text
    assert "Coaching recommendations" in text


def test_missing_profile_falls_back_to_defaults(tmp_path: Path) -> None:
    profile = load_profile(tmp_path / "does_not_exist.toml")
    assert profile == AIProfile()
    assert profile.extra_system_text() == ""


def test_invalid_profile_does_not_raise(tmp_path: Path) -> None:
    bad = tmp_path / "ai_profile.toml"
    bad.write_text("this is : not valid = toml ][", encoding="utf-8")
    profile = load_profile(bad)
    assert isinstance(profile, AIProfile)
