from fps_coach.chat_suggestions import suggest


def test_flick_topic_offers_fast_flicks_preset_when_base_present() -> None:
    items = suggest("how do I improve my flicks?", has_base_curve=True)
    assert any(s["kind"] == "preset" and s["payload"] == "fast_flicks" for s in items)


def test_presets_hidden_without_base_curve_but_prompts_remain() -> None:
    items = suggest("how do I improve my flicks?", has_base_curve=False)
    assert all(s["kind"] != "preset" for s in items)
    assert any(s["kind"] == "prompt" for s in items)


def test_tracking_topic() -> None:
    items = suggest("help me with tracking moving targets", has_base_curve=True)
    assert any(s["payload"] == "tracking" for s in items if s["kind"] == "preset")


def test_sensitivity_direction_keywords() -> None:
    faster = suggest("everything feels too slow", has_base_curve=True)
    assert any(s["payload"] == "raise_sens" for s in faster if s["kind"] == "preset")
    slower = suggest("my aim is too fast and twitchy", has_base_curve=True)
    assert any(s["payload"] == "lower_sens" for s in slower if s["kind"] == "preset")


def test_empty_text_returns_sensible_defaults() -> None:
    items = suggest("", has_base_curve=False)
    assert items  # never empty
    assert all(s["kind"] == "prompt" for s in items)


def test_results_are_capped_and_unique() -> None:
    items = suggest("flick tracking precision sensitivity curve", has_base_curve=True, limit=4)
    assert len(items) <= 4
    keys = [(s["kind"], s["payload"]) for s in items]
    assert len(keys) == len(set(keys))
