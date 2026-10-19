"""Scenario-based training module definitions.

Each scenario is an in-app target drill (click targets inside the app) tuned to a
specific skill: flicking, precision, reaction, or tracking. The interactive canvas
(UI) reads these parameters to run the drill; nothing here touches a game.

Pure/standard-library only - unit-tested without Qt.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Scenario:
    key: str
    name: str
    description: str
    target_count: int
    target_radius: int          # pixels
    moving: bool = False
    speed: float = 0.0          # px/sec for moving targets
    spawn_delay_ms: int = 0     # randomized delay before a target appears (reaction)


SCENARIOS: dict[str, Scenario] = {
    "flick": Scenario(
        "flick", "Flick practice",
        "Targets pop up around the center - flick to each and click. Trains fast, "
        "accurate direction changes.",
        target_count=12, target_radius=22,
    ),
    "precision": Scenario(
        "precision", "Precision micro-aim",
        "Small targets for careful, controlled micro-adjustments.",
        target_count=15, target_radius=12,
    ),
    "reaction": Scenario(
        "reaction", "Reaction time",
        "Targets appear after a random delay - click as fast as you can. Measures "
        "pure reaction time.",
        target_count=10, target_radius=26, spawn_delay_ms=1200,
    ),
    "tracking": Scenario(
        "tracking", "Tracking",
        "A moving target - keep your crosshair on it and click to confirm hits. "
        "Trains smooth tracking.",
        target_count=8, target_radius=20, moving=True, speed=190.0,
    ),
}

DEFAULT_ORDER = list(SCENARIOS.keys())


def list_scenarios() -> list[Scenario]:
    return [SCENARIOS[key] for key in DEFAULT_ORDER]


def get(key: str) -> Scenario:
    return SCENARIOS[key]
