"""Persistent history of calibration results.

Each completed calibration is scored into a single 0-100 number (so the user can
see progress at a glance) and appended to a JSON history file - previous results
are kept, never overwritten. The UI reads :meth:`CalibrationHistory.latest` and
:meth:`CalibrationHistory.previous` to show the most recent score next to the one
before it.

Pure/standard-library only so it can be unit-tested without Qt.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

MAX_RECORDS = 100


def _clamp01(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def compute_score(summary: dict) -> float:
    """Collapse a calibration summary into a single 0-100 score.

    Rewards path efficiency; penalizes overshoot, corrections, tremor and
    under-reach. Deterministic and bounded.
    """
    path_eff = _clamp01(summary.get("path_efficiency", 0.0))
    overshoot = _clamp01(summary.get("overshoot_score", 0.0))
    correction = _clamp01(summary.get("correction_score", 0.0))
    tremor = _clamp01(summary.get("tremor_score", 0.0))
    underreach = _clamp01(summary.get("underreach_score", 0.0))
    penalty = 0.35 * overshoot + 0.25 * correction + 0.20 * tremor + 0.20 * underreach
    score = 100.0 * (0.5 * path_eff + 0.5 * (1.0 - penalty))
    return round(max(0.0, min(100.0, score)), 1)


@dataclass(slots=True)
class CalibrationRecord:
    timestamp: str
    score: float
    trial_count: int = 0
    path_efficiency: float = 0.0
    overshoot_score: float = 0.0
    correction_score: float = 0.0
    title: str = ""
    sensitivity_multiplier: float = 1.0

    @classmethod
    def from_result(cls, summary: dict, recommendation: dict | None = None) -> CalibrationRecord:
        recommendation = recommendation or {}
        return cls(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            score=compute_score(summary),
            trial_count=int(summary.get("trial_count", 0) or 0),
            path_efficiency=float(summary.get("path_efficiency", 0.0) or 0.0),
            overshoot_score=float(summary.get("overshoot_score", 0.0) or 0.0),
            correction_score=float(summary.get("correction_score", 0.0) or 0.0),
            title=str(recommendation.get("title", "")),
            sensitivity_multiplier=float(recommendation.get("sensitivity_multiplier", 1.0) or 1.0),
        )

    def when(self) -> str:
        """Human-friendly short timestamp (falls back to the raw value)."""
        try:
            return datetime.fromisoformat(self.timestamp).strftime("%b %d, %H:%M")
        except ValueError:
            return self.timestamp


@dataclass
class CalibrationHistory:
    path: Path | None = None
    records: list[CalibrationRecord] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None) -> CalibrationHistory:
        history = cls(path=Path(path) if path else None)
        if history.path and history.path.exists():
            try:
                data = json.loads(history.path.read_text(encoding="utf-8"))
                history.records = [CalibrationRecord(**item) for item in data]
            except (OSError, ValueError, TypeError):
                history.records = []
        return history

    def add(self, summary: dict, recommendation: dict | None = None) -> CalibrationRecord:
        record = CalibrationRecord.from_result(summary, recommendation)
        self.records.append(record)
        if len(self.records) > MAX_RECORDS:
            self.records = self.records[-MAX_RECORDS:]
        self.save()
        return record

    def latest(self) -> CalibrationRecord | None:
        return self.records[-1] if self.records else None

    def previous(self) -> CalibrationRecord | None:
        return self.records[-2] if len(self.records) >= 2 else None

    def recent(self, count: int = 5) -> list[CalibrationRecord]:
        """Most recent first."""
        return list(reversed(self.records[-count:]))

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.write_text(
                json.dumps([asdict(r) for r in self.records], indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
