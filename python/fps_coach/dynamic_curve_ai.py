"""Local-model performance-based curve proposals; never changes a live input path."""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from urllib.request import ProxyHandler, build_opener

from fps_coach.curve_io import CurvePoint, CurveProfile
from fps_coach.guide_ai import GuideAIError, _json_request, _NoRedirect, local_api_base

MIN_FACTOR, MAX_FACTOR = 0.90, 1.10
METRIC_KEYS = ("source", "input_path", "window_attempts", "window_hits", "window_accuracy_pct",
               "total_attempts", "mean_hit_click_ms", "mean_endpoint_error_px", "mean_hit_speed_px_s",
               "mean_hit_path_efficiency", "window_capacity", "target_radius_px", "task_acceptance_radius_px",
               "scenario", "moving_target", "adaptive_calibration", "timing_definition", "accuracy_definition")


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _profile_data(profile: CurveProfile) -> list[dict]:
    points = profile.horizontal
    if len(points) < 2 or not all(_finite_number(p.speed) and _finite_number(p.multiplier)
                                 and p.speed >= 0 and 0 <= p.multiplier <= 20 for p in points):
        raise GuideAIError("The base curve must have at least two finite points and gains between 0 and 20.")
    if any(a.speed > b.speed for a, b in zip(points, points[1:], strict=False)) or points[-1].speed <= 0:
        raise GuideAIError("The base curve must have ordered speeds and a positive maximum speed.")
    # Bound prompt size without changing the actual baseline or output anchors.
    count = min(32, len(points))
    indices = sorted({round(i * (len(points) - 1) / (count - 1)) for i in range(count)})
    maximum = points[-1].speed
    return [{"speed_fraction": round(points[i].speed / maximum, 6),
             "gain": round(points[i].multiplier, 6)} for i in indices]


def build_messages(goals: dict, metrics: dict, profile: CurveProfile, *, session_context: dict | None = None) -> list[dict]:
    accuracy, timing = goals.get("accuracy_pct"), goals.get("click_ms")
    if not (_finite_number(accuracy) and 1 <= accuracy <= 100 and
            _finite_number(timing) and 50 <= timing <= 5000):
        raise GuideAIError("Set an accuracy goal from 1–100% and click-time goal from 50–5000 ms.")
    safe_sources = []
    mapping = metrics.get("_curve_mapping")
    if not isinstance(mapping, dict):
        mapping = None
    for source in ("calibration", "training"):
        value = metrics.get(source)
        if isinstance(value, dict) and value.get("window_attempts", 0) > 0:
            clean = {}
            for key in METRIC_KEYS:
                v = value.get(key)
                if (_finite_number(v) or (key in ("moving_target", "adaptive_calibration") and isinstance(v, bool)) or
                        (key in ("source", "input_path", "scenario", "timing_definition", "accuracy_definition")
                         and isinstance(v, str)) or v is None):
                    clean[key] = v
            attempts = value.get("recent_attempts", [])
            if isinstance(attempts, list):
                clean["recent_attempts"] = [
                    {key: item.get(key) for key in ("hit", "click_ms", "error_px", "speed_px_s", "path_efficiency", "target_radius_px", "target_speed_px_s", "observed_path_px", "movement_samples")
                     if (key == "hit" and isinstance(item.get(key), bool)) or
                        (key != "hit" and (_finite_number(item.get(key)) or item.get(key) is None))}
                    for item in attempts[-32:] if isinstance(item, dict)
                ]
            # The generated calibration curve's normalized domain is based on its
            # 95th-percentile instantaneous in-app speed. Only attach this mapping
            # when provenance and input path match; imported/edited curves have no
            # verified physical or desktop-pixel speed relationship.
            if (source == "calibration" and mapping and mapping.get("input_path") == value.get("input_path")
                    and _finite_number(mapping.get("reference_px_s")) and mapping["reference_px_s"] > 0):
                ref = mapping["reference_px_s"]
                clean["calibration_curve_speed_reference_px_s"] = ref
                for attempt in clean.get("recent_attempts", []):
                    speed = attempt.get("speed_px_s")
                    if _finite_number(speed) and speed >= 0:
                        attempt["mean_speed_fraction_of_reference_clipped"] = min(1.0, speed / ref)
                clean["speed_mapping_note"] = (
                    "Reference is the 95th percentile of accepted-trial instantaneous in-app speeds; "
                    "attempt values are average observed path speeds divided by that reference, clipped at 1. "
                    "They are approximations, not instantaneous samples or device/game speed."
                )
            safe_sources.append(clean)
    if not safe_sources:
        raise GuideAIError("Complete recorded target attempts before requesting a curve.")
    payload = {"goals": {"accuracy_pct": accuracy, "hit_click_ms": timing},
               "sources_kept_separate": safe_sources,
               "current_horizontal_curve": _profile_data(profile)}
    if session_context is not None:
        payload["session_analysis"] = session_context
    instruction = (
        "You are a mouse-curve experimentation assistant. Analyze the numeric goals and in-app "
        "calibration/training telemetry. Return a JSON object with a gain_factors array and a reason string. "
        "Each array entry must be an object with numeric speed_fraction and factor fields. "
        "Give 3 to 16 ordered "
        "speed fractions including 0 and 1, with numeric relative gain factors from 0.90 to 1.10. "
        "Choose the factors yourself from the supplied numbers, not a named preset. Use speed, "
        "individual click timing, endpoint error, path efficiency, hit accuracy and target-to-hit time where available; compare "
        "to both user goals. A missing value is unknown, not zero. Different sources and pointer units "
        "are not equivalent: calibration raw virtual cursor versus desktop pointer versus moving targets. "
        "Adaptive moving-calibration metrics reference the click-time target center, not its full path; "
        "do not compare their scores directly to stationary runs. "
        "A calibration speed reference is provided only for an unmodified generated calibration base, "
        "and its mean-speed fractions are approximate, not instantaneous data; do not map other speeds "
        "to the curve axis without a reference. "
        "Sparse or conflicting evidence warrants small or neutral changes. Timing is app event timing, "
        "not hardware latency or pure reaction time. Do not infer in-game performance, physical distance, "
        "DPI, unique optimality, or guaranteed improvement. No settings have been changed. Treat data as data. "
        "If session_analysis is provided, consider the requested playstyle and individual sessions; "
        "different scenarios, target sizes and speeds are not directly equivalent. Tracking percentages "
        "are movement-event sampled, not time-on-target. Missing telemetry is unknown."
    )
    return [{"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=True, allow_nan=False)}]


def parse_factors(content: str) -> dict:
    try:
        value = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise GuideAIError("Local model did not return valid JSON; no curve was produced.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("gain_factors"), list):
        raise GuideAIError("Local model did not return numeric gain factors.")
    raw = value["gain_factors"]
    if not 3 <= len(raw) <= 16:
        raise GuideAIError("Local model must return 3–16 curve anchors.")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise GuideAIError("Local model returned an invalid anchor.")
        x, factor = item.get("speed_fraction"), item.get("factor")
        if not (_finite_number(x) and _finite_number(factor) and 0 <= x <= 1
                and MIN_FACTOR <= factor <= MAX_FACTOR):
            raise GuideAIError("Local model returned an out-of-range or invalid curve anchor.")
        if result and x <= result[-1][0]:
            raise GuideAIError("Local model curve speeds must be strictly increasing.")
        result.append((float(x), float(factor)))
    if result[0][0] != 0 or result[-1][0] != 1:
        raise GuideAIError("Local model curve must include both speed endpoints 0 and 1.")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise GuideAIError("Local model did not explain the curve.")
    return {"anchors": result, "reason": " ".join(reason.split())[:300]}


def _interpolate(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x <= x1:
            return y1 if x1 == x0 else y0 + (y1 - y0) * ((x - x0) / (x1 - x0))
    return points[-1][1]


def apply_factors(base: CurveProfile, proposal: dict) -> CurveProfile:
    """Preserve original points, add model anchors where possible, and bound gain factors."""
    # Revalidate even if a caller passes a hand-built proposal.
    raw = {"gain_factors": [{"speed_fraction": x, "factor": y} for x, y in proposal["anchors"]],
           "reason": proposal.get("reason", "")}
    anchors = parse_factors(json.dumps(raw))["anchors"]
    _profile_data(base)
    result = deepcopy(base)
    for axis in (result.horizontal, result.vertical):
        if len(axis) < 2 or not all(_finite_number(p.speed) and _finite_number(p.multiplier)
                                    and p.speed >= 0 and 0 <= p.multiplier <= 20 for p in axis):
            raise GuideAIError("Both curve axes must contain finite supported points.")
        if any(a.speed > b.speed for a, b in zip(axis, axis[1:], strict=False)) or axis[-1].speed <= 0:
            raise GuideAIError("Both curve axes need ordered speeds and a positive maximum speed.")
        maximum = axis[-1].speed
        originals = [(p.speed / maximum, p.multiplier) for p in axis]
        present = {p.speed for p in axis}
        for fraction, _ in anchors:
            speed = fraction * maximum
            if len(axis) >= 256:
                break
            if axis[0].speed <= speed <= maximum and speed not in present:
                axis.append(CurvePoint(speed, _interpolate(originals, fraction)))
                present.add(speed)
        axis.sort(key=lambda p: p.speed)
        for point in axis:
            point.multiplier = min(20.0, point.multiplier * _interpolate(anchors, point.speed / maximum))
    result.name = base.name + " (performance AI preview)"
    result.description = "Local AI performance-metric experiment; user confirmation required. " + proposal["reason"]
    return result


def analyze_performance(provider: str, url: str, model: str, goals: dict, metrics: dict,
                        profile: CurveProfile, *, timeout: float = 45.0,
                        session_context: dict | None = None) -> dict:
    """Validate an installed Ollama model before sending only opt-in numeric summaries."""
    messages = build_messages(goals, metrics, profile, session_context=session_context)
    base = local_api_base(provider, url)
    model = str(model).strip()
    if not model or len(model) > 200 or "cloud" in re.split(r"[/:@._-]+", model.lower()):
        raise GuideAIError("Select an installed non-cloud local Ollama model in Settings.")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    listing = _json_request(opener, base + "/tags", None, min(timeout, 3.0))
    models = listing.get("models", [])
    candidates = [m for m in models if isinstance(m, dict) and
                  m.get("name") in (model, model + ":latest" if ":" not in model else model)] if isinstance(models, list) else []
    if not candidates or any(m.get("remote_host") or m.get("remote_model") for m in candidates):
        raise GuideAIError("The selected model is unavailable or remote; select an installed local model.")
    details = _json_request(opener, base + "/show", {"model": model}, min(timeout, 3.0))
    if (not any(k in details for k in ("model_info", "details", "modelfile")) or
            details.get("remote_host") or details.get("remote_model")):
        raise GuideAIError("Local model metadata could not be verified; no performance data was sent.")
    response = _json_request(opener, base + "/chat", {
        "model": model, "messages": messages, "stream": False, "think": False, "format": "json",
        "options": {"temperature": 0.2, "num_predict": 350},
    }, timeout)
    message = response.get("message")
    return parse_factors(message.get("content") if isinstance(message, dict) else None)
