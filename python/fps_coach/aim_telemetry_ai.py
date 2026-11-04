"""Opt-in in-app aim summaries and local-model playstyle experiments.

Summary statistics are deterministic observations, not an AI or a guarantee.
Only an explicitly requested local-model response generates a curve proposal.
"""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass

from .dynamic_curve_ai import analyze_performance
from .guide_ai import GuideAIError

PLAYSTYLES = ("balanced", "flick", "tracking", "precision")
SCENARIOS = {"flick", "precision", "precision_steady", "reaction", "tracking"}


def _number(value, *, minimum=0.0, maximum=1_000_000.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and minimum <= value <= maximum else None


def _count(value, maximum=1_000_000):
    number = _number(value, maximum=maximum)
    return int(number) if number is not None and number == int(number) else None


def analyze_aim_telemetry(telemetry: dict, targets: dict | None = None) -> dict:
    """Compare observed click and moving-target event summaries with target goals.

    Input and output contain aggregate counts/metrics only. Values not measured
    stay None. Endpoint error is radial in in-app pixels / target radii; tracking
    is pointer-move-event weighted, not the fraction of elapsed time on target.
    """
    if not isinstance(telemetry, dict):
        raise ValueError("Aim telemetry must be a summary dictionary.")
    attempts = _count(telemetry.get("attempts"))
    hits = _count(telemetry.get("hits"))
    if attempts is None or hits is None or hits > attempts:
        raise ValueError("Aim telemetry needs valid attempt and hit counts.")
    accuracy = 100.0 * hits / attempts if attempts else None
    click_ms = _number(telemetry.get("mean_hit_click_ms"), maximum=3_600_000)
    error_px = _number(telemetry.get("mean_endpoint_error_px"))
    error_ratio = _number(telemetry.get("mean_endpoint_error_radii"), maximum=10_000)
    track_count = _count(telemetry.get("tracking_event_count")) or 0
    track_pct = _number(telemetry.get("tracking_on_target_pct"), maximum=100) if track_count else None
    track_error = _number(telemetry.get("tracking_mean_error_radii"), maximum=10_000) if track_count else None
    track_error_px = _number(telemetry.get("tracking_mean_error_px")) if track_count else None
    radius = _number(telemetry.get("target_radius_px"))
    goals = targets if isinstance(targets, dict) else {}
    accuracy_goal = _number(goals.get("accuracy_pct"), minimum=1, maximum=100)
    click_goal = _number(goals.get("click_ms"), minimum=50, maximum=5000)
    return {
        "attempts": attempts, "hits": hits, "accuracy_pct": accuracy,
        "accuracy_delta_percentage_points": (accuracy - accuracy_goal
                                              if accuracy is not None and accuracy_goal is not None else None),
        "mean_hit_click_ms": click_ms,
        "hit_click_time_delta_ms": (click_ms - click_goal if click_ms is not None and click_goal is not None else None),
        "mean_endpoint_error_px": error_px, "mean_endpoint_error_radii": error_ratio,
        "target_radius_px": radius, "target_speed_px_s": _number(telemetry.get("target_speed_px_s")),
        "moving_target": telemetry.get("moving_target") is True,
        "tracking_event_count": track_count, "tracking_on_target_pct": track_pct,
        "tracking_mean_error_radii": track_error, "tracking_mean_error_px": track_error_px,
        "observation_partial": telemetry.get("observation_partial") is True,
        "limitations": "Click times are app-event times; tracking is move-event weighted, not time-on-target. No raw paths or game input.",
    }


def _session_record(item, goals):
    if is_dataclass(item) and not isinstance(item, type):
        item = asdict(item)
    if not isinstance(item, dict):
        return None
    hits, misses = _count(item.get("hits")), _count(item.get("misses"))
    raw_scenario = item.get("scenario")
    scenario = raw_scenario if isinstance(raw_scenario, str) and raw_scenario in SCENARIOS else "other"
    if hits is None or misses is None or hits + misses <= 0:
        return None
    # Existing sessions without monitoring still contribute real hit/click stats.
    avg = _number(item.get("avg_reaction_ms"), maximum=3_600_000)
    result = {"scenario": scenario, "hits": hits, "misses": misses,
              "accuracy_pct": 100 * hits / (hits + misses),
              "mean_hit_click_ms": avg if hits and avg and avg > 0 else None}
    data = item.get("aim_telemetry")
    if isinstance(data, dict):
        try:
            result["observed_aim"] = analyze_aim_telemetry(data, goals)
        except ValueError:
            pass
    return result


def recommend_sensitivity_profile(provider: str, url: str, model: str, sessions, playstyle: str,
                                  profile, goals: dict, *, timeout: float = 45.0) -> dict:
    """Ask verified loopback Ollama for bounded, preview-only gain anchors.

    Existing and opt-in saved summaries remain separate per session. The model
    selects factors; there is no silent heuristic substitute or auto-application.
    """
    if playstyle not in PLAYSTYLES:
        raise GuideAIError("Choose a supported playstyle.")
    if not isinstance(sessions, (list, tuple)):
        raise GuideAIError("Training session history is unavailable.")
    records = [entry for s in sessions[-30:] if (entry := _session_record(s, goals)) is not None]
    attempts = sum(r["hits"] + r["misses"] for r in records)
    hits = sum(r["hits"] for r in records)
    if attempts < 3 or hits < 1:
        raise GuideAIError("Complete at least three training attempts, including a hit, before requesting analysis.")
    metrics = {"training": {"source": "training", "input_path": "desktop",
                            "window_attempts": attempts, "window_hits": hits,
                            "window_accuracy_pct": 100 * hits / attempts,
                            "total_attempts": attempts}}
    context = {"playstyle": playstyle, "session_count": len(records), "sessions": records,
               "sessions_order": "oldest to newest",
               "comparison_note": "Scenario and target settings can differ; basic historical sessions may have stopped early. Performance is not proof of causation or an optimal profile."}
    result = analyze_performance(provider, url, model, goals, metrics, profile,
                                 timeout=timeout, session_context=context)
    return {**result, "session_count": len(records), "playstyle": playstyle}
