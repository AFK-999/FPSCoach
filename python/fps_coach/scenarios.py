"""Scenario-based training module definitions.

Each scenario is an in-app target drill (click targets inside the app) tuned to a
specific skill: flicking, precision, reaction, or tracking. The interactive canvas
(UI) reads these parameters to run the drill; nothing here touches a game.

Pure/standard-library only - unit-tested without Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


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
    "precision_steady": Scenario(
        "precision_steady", "Precision",
        "Medium targets for steady, accurate clicks. Less fine adjustment than micro-aim.",
        target_count=15, target_radius=18,
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

# Training tuning. Speed is a percentage of the drill's own pace (50% to 200%).
# Difficulty is a level from 1 (easiest, largest targets) to 5 (hardest, smallest).
SPEED_MIN_PCT, SPEED_MAX_PCT, SPEED_DEFAULT_PCT = 50, 200, 100
DIFFICULTY_MIN, DIFFICULTY_MAX, DIFFICULTY_DEFAULT = 1, 5, 3
DIFFICULTY_RADIUS_SCALE = {1: 1.4, 2: 1.2, 3: 1.0, 4: 0.85, 5: 0.7}
_MIN_RADIUS = 4


def tuned(scenario: Scenario, speed_pct: int = SPEED_DEFAULT_PCT,
          difficulty: int = DIFFICULTY_DEFAULT) -> Scenario:
    """Return a copy of ``scenario`` with the speed and difficulty settings applied.

    * Speed scales a moving target's velocity, and shortens or lengthens the random
      wait before a reaction target appears (a faster setting means a shorter wait).
      Drills without movement or delay are unchanged by speed.
    * Difficulty scales the target radius. Level 3 keeps the drill's own size.
    Out-of-range values are clamped, so the result is always playable.
    """
    pct = min(max(int(speed_pct), SPEED_MIN_PCT), SPEED_MAX_PCT)
    level = min(max(int(difficulty), DIFFICULTY_MIN), DIFFICULTY_MAX)
    factor = pct / 100.0
    radius = max(_MIN_RADIUS, round(scenario.target_radius * DIFFICULTY_RADIUS_SCALE[level]))
    delay = round(scenario.spawn_delay_ms / factor) if scenario.spawn_delay_ms else 0
    return replace(
        scenario,
        target_radius=radius,
        speed=scenario.speed * factor,
        spawn_delay_ms=delay,
    )


def list_scenarios() -> list[Scenario]:
    return [SCENARIOS[key] for key in DEFAULT_ORDER]


def get(key: str) -> Scenario:
    return SCENARIOS[key]
