from fps_coach import scenarios
from fps_coach.analytics import AnalyticsStore, SessionResult, summarize
from fps_coach.crosshair import PRESETS, CrosshairConfig


# --- scenarios -------------------------------------------------------------
def test_scenarios_catalog() -> None:
    keys = {s.key for s in scenarios.list_scenarios()}
    assert {"flick", "precision", "reaction", "tracking"} <= keys
    assert scenarios.get("tracking").moving is True
    assert scenarios.get("flick").target_count > 0


# --- analytics -------------------------------------------------------------
def test_summarize_accuracy_and_reaction() -> None:
    s = summarize([200.0, 300.0, 250.0], hits=3, misses=1)
    assert s["hits"] == 3 and s["misses"] == 1
    assert abs(s["accuracy"] - 0.75) < 1e-6
    assert s["best_reaction_ms"] == 200.0
    assert abs(s["avg_reaction_ms"] - 250.0) < 1e-6


def test_summarize_handles_empty() -> None:
    s = summarize([], hits=0, misses=0)
    assert s["accuracy"] == 0.0 and s["avg_reaction_ms"] == 0.0


def test_analytics_store_persist_and_aggregate(tmp_path) -> None:
    path = tmp_path / "a.json"
    store = AnalyticsStore.load(path)
    assert store.aggregate()["total"] == 0
    store.add(SessionResult.create("flick", [220, 240, 260], hits=3, misses=1))
    store.add(SessionResult.create("flick", [200, 210], hits=2, misses=0))
    agg = store.aggregate()
    assert agg["total"] == 2
    assert 0.0 < agg["avg_accuracy"] <= 1.0
    assert agg["best_reaction_ms"] == 200.0
    # Reloads from disk.
    reloaded = AnalyticsStore.load(path)
    assert reloaded.aggregate()["total"] == 2
    assert reloaded.by_scenario("flick")["count"] == 2


# --- crosshair -------------------------------------------------------------
def test_crosshair_normalize_clamps_and_validates() -> None:
    cfg = CrosshairConfig(shape="bogus", size=999, thickness=0, color="notahex", opacity=5).normalized()
    assert cfg.shape == "cross_dot"
    assert cfg.size <= 64 and cfg.thickness >= 1
    assert cfg.color == "#70e1ba"
    assert cfg.opacity <= 1.0


def test_crosshair_roundtrip() -> None:
    cfg = CrosshairConfig(shape="circle", size=14, color="#ff8800")
    data = cfg.to_dict()
    restored = CrosshairConfig.from_dict(data)
    assert restored.shape == "circle"
    assert restored.color == "#ff8800"


def test_crosshair_presets_exist() -> None:
    assert {"classic_cross", "dot", "cross_dot", "circle"} <= set(PRESETS)
