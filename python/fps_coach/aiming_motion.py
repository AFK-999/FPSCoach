"""Self-reported aiming-motion context, not a biomechanical measurement or curve model."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MotionGuidance:
    key: str
    label: str
    caption: str
    flick: str
    tracking: str
    fatigue: str


OPTIONS: tuple[MotionGuidance, ...] = (
    MotionGuidance(
        "wrist", "Wrist", "Pivot at the wrist",
        "A small pivot can make short flicks feel quick; range may be limited.",
        "Can support small corrections, but steady control varies by person and setup.",
        "Repeated wrist motion or tension may become tiring; pause if uncomfortable.",
    ),
    MotionGuidance(
        "fingers", "Fingers", "Small finger motions",
        "Small finger movements can support quick micro-flicks, with limited travel.",
        "Fine adjustments may feel precise, though grip and practice affect control.",
        "A tight grip or repeated finger movement can be tiring; relax and take breaks.",
    ),
    MotionGuidance(
        "arm", "Arm", "Forearm and elbow",
        "Larger forearm movement can cover wider flicks but needs desk space.",
        "Arm movement can support longer tracking; small corrections need practice.",
        "Long reaches or sustained arm tension may be tiring; keep movement comfortable.",
    ),
    MotionGuidance(
        "shoulder_hybrid", "Shoulder / Hybrid", "Larger motion or a mix",
        "Shoulder or combined motions can cover larger distances; transitions vary.",
        "Mixing joints may help cover different ranges, but coordination takes practice.",
        "Load may be shared, yet shoulder or grip tension can still cause fatigue.",
    ),
)
BY_KEY = {option.key: option for option in OPTIONS}


def motion_context(key: str | None) -> str:
    """Give the coach user-reported context without claiming body measurement."""
    option = BY_KEY.get(key or "")
    if option is None:
        return ""
    return (
        f"User-reported primary flick motion: {option.label}. This is optional self-report, "
        "not a measured joint contribution, diagnosis, fatigue level, or performance score. "
        "Most people use a mix of joints. The measured analyzer is not adjusted from this label; "
        "a separate AI proposal may be previewed and requires explicit user confirmation. "
        "Explain flicking, tracking, and possible fatigue as individual-dependent trade-offs; "
        "do not infer pain, injury, skill, or an optimal sensitivity from the selection. "
        "Refer to current-session calibration metrics only if actually supplied."
    )


def motion_question(key: str | None) -> str:
    option = BY_KEY.get(key or "")
    if option is None:
        return ""
    return (
        f"I mostly use {option.label} when flicking. How might that affect flicking, "
        "tracking and fatigue? Use my current calibration results if available, distinguish "
        "measured facts from general possibilities, and suggest one comfortable way to test."
    )
