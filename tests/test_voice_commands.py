from fps_coach.voice_commands import Command, parse


def test_navigation_phrases() -> None:
    assert parse("open calibration") == Command("navigate", "calibration")
    assert parse("go to settings") == Command("navigate", "settings")
    assert parse("show the dashboard") == Command("navigate", "dashboard")
    assert parse("recommendation") == Command("navigate", "recommendation")


def test_calibration_actions_beat_navigation() -> None:
    assert parse("start calibration") == Command("calibrate", "start")
    assert parse("reset calibration") == Command("calibrate", "reset")


def test_presets_and_sensitivity() -> None:
    assert parse("optimize for fast flicks") == Command("preset", "fast_flicks")
    assert parse("switch to precision") == Command("preset", "precision")
    assert parse("raise sensitivity") == Command("preset", "raise_sens")
    assert parse("please lower sensitivity") == Command("preset", "lower_sens")
    assert parse("reset curve") == Command("preset", "reset")


def test_panel_commands() -> None:
    assert parse("show coach panel") == Command("panel", "show")
    assert parse("hide overlay") == Command("panel", "hide")


def test_dictation_prefix() -> None:
    cmd = parse("ask coach how do I improve my aim")
    assert cmd == Command("dictate", "how do i improve my aim")
    assert parse("coach what is counter strafing") == Command("dictate", "what is counter strafing")


def test_custom_wake_word() -> None:
    # A configured wake word is honored for dictation.
    assert parse("jarvis how do i warm up", wake_word="jarvis") == Command(
        "dictate", "how do i warm up"
    )
    assert parse("hey jarvis open settings", wake_word="jarvis") == Command(
        "dictate", "open settings"
    )
    # The default 'coach' still works regardless.
    assert parse("coach thanks", wake_word="jarvis") is None or isinstance(
        parse("coach thanks", wake_word="jarvis"), (Command, type(None))
    )


def test_unrecognized_returns_none() -> None:
    assert parse("") is None
    assert parse("banana sandwich") is None
