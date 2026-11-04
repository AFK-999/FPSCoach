"""In-Game Mode: game selection and sensitivity conversion (no game access).

In-Game Mode lets the user tell Vantage **which game** they are practising and
**what sensitivity they use in it**, so the app can express its existing
calibration recommendation in that game's own numbers — for example "your 40.8
cm/360° is VALORANT 0.400; the recommended 1.06x would be 0.424 (38.5 cm/360°)".

Boundary: this module performs arithmetic on values the **user types**. It does
not read, hook, or write any game process, window, memory, or configuration
file, it does not capture the game, and it never applies a setting. The mode is
off at every launch (``InGameMode.enabled`` starts ``False`` and the flag is
never persisted); only the game choice, DPI, and the user's own sensitivity
number are remembered.

Sensitivity maths
-----------------
Each game turns the mouse by ``yaw`` degrees per mouse *count* at in-game
sensitivity 1.0. From that::

    counts_per_360 = 360 / (yaw * sensitivity)
    cm_per_360     = counts_per_360 / dpi * 2.54
    sensitivity    = 360 * 2.54 / (yaw * dpi * cm_per_360)

Published yaw values differ slightly between patches, so the catalog values are
starting points and the UI always shows the resulting cm/360°. A user who
measures their own 360° distance gets an *effective* yaw derived from that
measurement, which absorbs any catalogue error for their setup.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DEFAULT_GAME_KEY",
    "GAMES",
    "GameProfile",
    "InGameMode",
    "counts_per_360",
    "cm_per_360",
    "effective_yaw",
    "find_game",
    "sensitivity_for_cm_per_360",
]

#: Degrees of turn per mouse count at in-game sensitivity 1.0.
DEFAULT_YAW = 0.022


@dataclass(frozen=True, slots=True)
class GameProfile:
    """One title's sensitivity scale.

    ``yaw`` is the published degrees-per-count constant at sensitivity 1.0; it
    is an approximation used to seed the conversion. ``note`` explains what the
    value covers (and what to do when a patch changes it).
    """

    key: str
    name: str
    yaw: float
    note: str = ""

    def __post_init__(self) -> None:  # pragma: no cover - data guard
        if self.yaw <= 0:
            raise ValueError(f"{self.key}: yaw must be positive, got {self.yaw!r}")


#: Curated catalog. Keys are stable and stored in QSettings as ``ingame/game``.
GAMES: tuple[GameProfile, ...] = (
    GameProfile(
        "cs2",
        "Counter-Strike 2",
        0.022,
        "Source-engine yaw (CS:GO, CS2, Titanfall 2, Apex-style scales).",
    ),
    GameProfile(
        "valorant",
        "VALORANT",
        0.07,
        "VALORANT's own scale; sensitivity values are much smaller than Source games.",
    ),
    GameProfile(
        "overwatch",
        "Overwatch 2",
        0.0066,
        "Overwatch scale; values are small and percent-like in the UI.",
    ),
    GameProfile(
        "apex",
        "Apex Legends",
        0.022,
        "Source-engine yaw; Apex's in-game slider is 1:1 with the underlying value.",
    ),
    GameProfile(
        "other",
        "Other / not listed",
        DEFAULT_YAW,
        "Fallback yaw. Measure your 360° distance below and the app uses that instead.",
    ),
)
DEFAULT_GAME_KEY = "cs2"


def find_game(key: str) -> GameProfile:
    """Return the catalog entry for ``key``, falling back to the last entry."""
    for game in GAMES:
        if game.key == key:
            return game
    return GAMES[-1]


def counts_per_360(yaw: float, sensitivity: float) -> float:
    """Mouse counts needed for a full 360° turn."""
    if yaw <= 0:
        raise ValueError(f"yaw must be positive, got {yaw!r}")
    if sensitivity <= 0:
        raise ValueError(f"sensitivity must be positive, got {sensitivity!r}")
    return 360.0 / (yaw * sensitivity)


def cm_per_360(yaw: float, dpi: float, sensitivity: float) -> float:
    """Physical mouse travel, in centimetres, for a full 360° turn."""
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi!r}")
    counts = counts_per_360(yaw, sensitivity)
    return counts / dpi * 2.54


def sensitivity_for_cm_per_360(yaw: float, dpi: float, cm360: float) -> float:
    """In-game sensitivity that yields ``cm360`` centimetres per 360°."""
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi!r}")
    if cm360 <= 0:
        raise ValueError(f"cm per 360 must be positive, got {cm360!r}")
    if yaw <= 0:
        raise ValueError(f"yaw must be positive, got {yaw!r}")
    return 360.0 * 2.54 / (yaw * dpi * cm360)


def effective_yaw(game: GameProfile, dpi: float, sensitivity: float, measured_cm360: float) -> float:
    """Yaw for this user: the catalogue value, or one derived from their measurement.

    A measured 360° distance is the ground truth for the user's own setup (it
    also absorbs acceleration-off rounding and DPI drift), so when it is given
    the conversion is pinned to it instead of to the catalogue constant.
    """
    if measured_cm360 <= 0:
        return game.yaw
    if dpi <= 0 or sensitivity <= 0:
        raise ValueError("dpi and sensitivity must be positive to derive a measured yaw")
    # cm360 = 360 * 2.54 / (yaw * dpi * sens)  ->  yaw = 360 * 2.54 / (cm360 * dpi * sens)
    return 360.0 * 2.54 / (measured_cm360 * dpi * sensitivity)


class InGameMode:
    """Session state for In-Game Mode.

    ``enabled`` is deliberately **not** a persisted setting: it starts ``False``
    on every construction (that is, every launch) and only the view that owns
    it can turn it on for the current session. Everything else here is plain
    arithmetic over the user's own numbers.

    This class performs no I/O and never touches a game: it is a small state
    holder so the UI and the AI context builder share one source of truth.
    """

    def __init__(
        self,
        game_key: str = DEFAULT_GAME_KEY,
        dpi: float = 800.0,
        sensitivity: float = 1.0,
        measured_cm360: float = 0.0,
    ) -> None:
        self._enabled = False  # never loaded from settings: off at every launch
        self._game = find_game(game_key)
        self._dpi = float(dpi)
        self._sensitivity = float(sensitivity)
        self._measured_cm360 = float(measured_cm360)

    # -- session toggle ----------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    # -- values ------------------------------------------------------------
    @property
    def game(self) -> GameProfile:
        return self._game

    @property
    def dpi(self) -> float:
        return self._dpi

    @property
    def sensitivity(self) -> float:
        return self._sensitivity

    @property
    def measured_cm360(self) -> float:
        """The user's measured 360° distance in cm, or 0 when not measured."""
        return self._measured_cm360

    def set_game(self, key: str) -> None:
        self._game = find_game(key)

    def set_dpi(self, dpi: float) -> None:
        if dpi <= 0:
            raise ValueError(f"dpi must be positive, got {dpi!r}")
        self._dpi = float(dpi)

    def set_sensitivity(self, sensitivity: float) -> None:
        if sensitivity <= 0:
            raise ValueError(f"sensitivity must be positive, got {sensitivity!r}")
        self._sensitivity = float(sensitivity)

    def set_measured_cm360(self, cm360: float) -> None:
        if cm360 < 0:
            raise ValueError(f"measured cm/360 must be >= 0, got {cm360!r}")
        self._measured_cm360 = float(cm360)

    # -- derived values ----------------------------------------------------
    @property
    def yaw(self) -> float:
        """Catalogue yaw, or the yaw implied by the user's own measurement."""
        return effective_yaw(self._game, self._dpi, self._sensitivity, self._measured_cm360)

    @property
    def counts_per_360(self) -> float:
        return counts_per_360(self.yaw, self._sensitivity)

    @property
    def cm_per_360(self) -> float:
        return cm_per_360(self.yaw, self._dpi, self._sensitivity)

    def sensitivity_for_cm360(self, cm360: float) -> float:
        return sensitivity_for_cm_per_360(self.yaw, self._dpi, cm360)

    def suggested_sensitivity(self, multiplier: float) -> float:
        """In-game sensitivity after applying the app's recommendation multiplier."""
        if multiplier <= 0:
            raise ValueError(f"multiplier must be positive, got {multiplier!r}")
        return self._sensitivity * float(multiplier)

    def suggestion_cm_per_360(self, multiplier: float) -> float:
        """Resulting cm/360° after applying ``multiplier`` (unchanged DPI)."""
        return self.cm_per_360 / float(multiplier)

    # -- text for the UI and the coach ------------------------------------
    def feedback(self, multiplier: float | None = None) -> str:
        """One or two sentences describing the current conversion for the UI."""
        base = (
            f"{self._game.name}: {self._sensitivity:.3f} at {self._dpi:.0f} DPI "
            f"= {self.cm_per_360:.1f} cm / 360°"
        )
        if multiplier is None or multiplier <= 0:
            return base
        suggested = self.suggested_sensitivity(multiplier)
        return (
            f"{base}. A {multiplier:.2f}× change is {suggested:.3f} "
            f"({self.suggestion_cm_per_360(multiplier):.1f} cm / 360°)."
        )

    def context_block(self, multiplier: float | None = None) -> str:
        """Compact block appended to the AI coach's context; empty when disabled."""
        if not self._enabled:
            return ""
        source = "measured" if self._measured_cm360 > 0 else "catalogue"
        lines = [
            "In-Game Mode is ON (user-enabled for this session only).",
            f"Game: {self._game.name} (yaw {self.yaw:.4f} deg per count at sensitivity 1.0, {source}).",
            f"Mouse DPI: {self._dpi:.0f}. In-game sensitivity: {self._sensitivity:.3f}.",
            f"Turn distance: {self.cm_per_360:.1f} cm per 360 degrees "
            f"({self.counts_per_360:.0f} counts).",
        ]
        if multiplier is not None and multiplier > 0:
            lines.append(
                f"The app's current sensitivity multiplier is {multiplier:.3f}. Applied to this "
                f"game's value that is {self.suggested_sensitivity(multiplier):.3f} "
                f"({self.suggestion_cm_per_360(multiplier):.1f} cm per 360 degrees)."
            )
        lines.append(
            "These numbers come from the user's own entries; the app cannot see the game. "
            "When asked, suggest a specific in-game sensitivity value the user would apply "
            "themselves, explain the trade-off, and keep the change small and reversible."
        )
        return "\n".join(lines)

    def question(self, multiplier: float | None = None) -> str:
        """A ready-made question for the Ask-the-coach button."""
        subject = (
            f"I play {self._game.name} at {self._sensitivity:.3f} with {self._dpi:.0f} DPI "
            f"({self.cm_per_360:.1f} cm per 360)."
        )
        if multiplier is not None and multiplier > 0:
            return (
                f"{subject} The app suggests a {multiplier:.2f}x change, which would be "
                f"{self.suggested_sensitivity(multiplier):.3f}. What should I try in game, and "
                "what should I watch for when I test it?"
            )
        return (
            f"{subject} What in-game sensitivity should I try, and what should I watch for "
            "when I test it?"
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"InGameMode(enabled={self._enabled}, game={self._game.key!r}, "
            f"dpi={self._dpi!r}, sensitivity={self._sensitivity!r})"
        )
