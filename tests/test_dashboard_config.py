from fps_coach.dashboard_config import (
    DEFAULT_ENABLED,
    DEFAULT_ORDER,
    TOOLS,
    move,
    normalize_enabled,
    normalize_order,
    tool_label,
)


def test_normalize_order_fills_and_filters() -> None:
    # Unknown keys dropped; missing catalog tools appended; known order kept.
    result = normalize_order(["chat", "bogus", "capture"])
    assert result[:2] == ["chat", "capture"]
    assert set(result) == set(DEFAULT_ORDER)
    assert "bogus" not in result


def test_normalize_order_none_gives_default() -> None:
    assert normalize_order(None) == DEFAULT_ORDER


def test_normalize_enabled_defaults_and_filters() -> None:
    assert normalize_enabled(None) == set(DEFAULT_ENABLED)
    assert normalize_enabled(["chat", "nope"]) == {"chat"}


def test_move_reorders_within_bounds() -> None:
    order = ["a", "b", "c"] if False else list(DEFAULT_ORDER)
    first = order[0]
    # Moving the first item up is a no-op.
    assert move(order, first, -1) == order
    # Moving it down swaps with the next.
    moved = move(order, first, 1)
    assert moved[1] == first
    # Unknown key is a no-op copy.
    assert move(order, "bogus", 1) == order


def test_tool_label_known_and_unknown() -> None:
    assert tool_label("chat") == TOOLS["chat"][0]
    assert tool_label("unknown") == "unknown"
