"""Performance analytics: aim accuracy + reaction times across training sessions.

A session produces a :class:`SessionResult` (accuracy, reaction-time stats). The
:class:`AnalyticsStore` persists sessions to JSON and computes aggregates (overall
and per-scenario) so the UI can show progress over time. All metrics come from the
app's own in-app drills - no game data is read.

Pure/standard-library only - unit-tested without Qt.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

MAX_SESSIONS = 500


def summarize(reaction_times_ms: list[float], hits: int, misses: int) -> dict:
    """Compute accuracy + reaction stats from a drill's raw results."""
    attempts = hits + misses
    accuracy = (hits / attempts) if attempts > 0 else 0.0
    valid = [float(r) for r in reaction_times_ms if r is not None and r >= 0]
    avg_rt = sum(valid) / len(valid) if valid else 0.0
    best_rt = min(valid) if valid else 0.0
    return {
        "hits": int(hits),
        "misses": int(misses),
        "accuracy": round(accuracy, 4),
        "avg_reaction_ms": round(avg_rt, 1),
        "best_reaction_ms": round(best_rt, 1),
    }


@dataclass(slots=True)
class SessionResult:
    timestamp: str
    scenario: str
    hits: int
    misses: int
    accuracy: float
    avg_reaction_ms: float
    best_reaction_ms: float

    @classmethod
    def create(cls, scenario: str, reaction_times_ms: list[float], hits: int, misses: int) -> SessionResult:
        s = summarize(reaction_times_ms, hits, misses)
        return cls(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            scenario=scenario,
            hits=s["hits"],
            misses=s["misses"],
            accuracy=s["accuracy"],
            avg_reaction_ms=s["avg_reaction_ms"],
            best_reaction_ms=s["best_reaction_ms"],
        )

    def when(self) -> str:
        try:
            return datetime.fromisoformat(self.timestamp).strftime("%b %d, %H:%M")
        except ValueError:
            return self.timestamp


@dataclass
class AnalyticsStore:
    path: Path | None = None
    sessions: list[SessionResult] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None) -> AnalyticsStore:
        store = cls(path=Path(path) if path else None)
        if store.path and store.path.exists():
            try:
                data = json.loads(store.path.read_text(encoding="utf-8"))
                store.sessions = [SessionResult(**item) for item in data]
            except (OSError, ValueError, TypeError):
                store.sessions = []
        return store

    def add(self, result: SessionResult) -> None:
        self.sessions.append(result)
        if len(self.sessions) > MAX_SESSIONS:
            self.sessions = self.sessions[-MAX_SESSIONS:]
        self.save()

    def recent(self, count: int = 8) -> list[SessionResult]:
        return list(reversed(self.sessions[-count:]))

    def aggregate(self) -> dict:
        if not self.sessions:
            return {"total": 0, "avg_accuracy": 0.0, "avg_reaction_ms": 0.0, "best_reaction_ms": 0.0}
        accs = [s.accuracy for s in self.sessions]
        rts = [s.avg_reaction_ms for s in self.sessions if s.avg_reaction_ms > 0]
        bests = [s.best_reaction_ms for s in self.sessions if s.best_reaction_ms > 0]
        return {
            "total": len(self.sessions),
            "avg_accuracy": round(sum(accs) / len(accs), 4),
            "avg_reaction_ms": round(sum(rts) / len(rts), 1) if rts else 0.0,
            "best_reaction_ms": round(min(bests), 1) if bests else 0.0,
        }

    def by_scenario(self, scenario: str) -> dict:
        subset = [s for s in self.sessions if s.scenario == scenario]
        if not subset:
            return {"count": 0}
        accs = [s.accuracy for s in subset]
        bests = [s.best_reaction_ms for s in subset if s.best_reaction_ms > 0]
        return {
            "count": len(subset),
            "avg_accuracy": round(sum(accs) / len(accs), 4),
            "best_reaction_ms": round(min(bests), 1) if bests else 0.0,
        }

    def clear(self) -> None:
        """Delete every stored session and write the empty store to disk."""
        self.sessions = []
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.write_text(
                json.dumps([asdict(s) for s in self.sessions], indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
