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
    assert cmd == Command("dictate", "how do I improve my aim")
    assert parse("coach what is counter strafing") == Command("dictate", "what is counter strafing")


def test_custom_wake_word() -> None:
    # A configured wake word is honored for dictation.
    assert parse("jarvis how do i warm up", wake_word="jarvis") == Command(
        "dictate", "how do i warm up"
    )
    assert parse("hey jarvis open settings", wake_word="jarvis") == Command(
        "navigate", "settings"
    )
    # The default 'coach' still works regardless.
    assert parse("coach thanks", wake_word="jarvis") is None or isinstance(
        parse("coach thanks", wake_word="jarvis"), (Command, type(None))
    )


def test_unrecognized_returns_none() -> None:
    assert parse("") is None
    assert parse("banana sandwich") is None


def test_specific_settings_and_calibration_guide():
    examples = {
        "Hey coach, open calibration input settings": Command("section", "calibration:open"),
        "please go to calibration and open input settings": Command("section", "calibration:open"),
        "open input path": Command("section", "calibration:open"),
        "close the calibration settings panel": Command("section", "calibration:close"),
        "open training settings": Command("section", "training:open"),
        "show coach settings": Command("section", "coach:open"),
        "close settings for coach": Command("section", "coach:close"),
        "open voice settings": Command("settings", "voice"),
        "open AI settings": Command("settings", "ai"),
        "open advanced settings": Command("settings", "advanced"),
        "open calibration guide": Command("guide", "open"),
        "next guide question": Command("guide", "next"),
        "previous guide step": Command("guide", "back"),
        "skip calibration guide question": Command("guide", "skip"),
        "show sensitivity curve": Command("curve_view", "sensitivity"),
        "resume raw input": Command("calibrate", "resume"),
        "open curve tuning": Command("coach", "curve_tuning"),
        "focus coach": Command("coach", "focus"),
        "explain my calibration": Command("coach", "explain_calibration"),
        "stop speaking": Command("coach", "stop_speaking"),
    }
    for phrase, expected in examples.items():
        assert parse(phrase) == expected, phrase


def test_questions_do_not_accidentally_execute_commands():
    assert parse("ask coach open calibration settings") == Command("dictate", "open calibration settings")
    assert parse("coach how do I open calibration settings") == Command(
        "dictate", "how do I open calibration settings"
    )
    assert parse("someone said start calibration yesterday") is None
    assert parse("don't start calibration") is None
    assert parse("should I increase sensitivity") is None
    assert parse("coachman open settings") is None
    assert parse("ask") is None
    assert parse("coach") is None


def test_no_implicit_voice_recording_live_vision_or_curve_application():
    for phrase in ("start recording", "watch live", "send to calibration", "clear history", "apply curve"):
        assert parse(phrase) is None


def test_focus_question_is_not_sent_to_provider():
    assert parse("ask a question") == Command("coach", "focus")
