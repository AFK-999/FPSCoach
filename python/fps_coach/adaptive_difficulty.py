"""Bounded, explainable real-time difficulty control for the in-app task.

This is a deterministic controller, not an LLM. Accuracy means clicks inside
the visible target / all in-arena clicks in a rolling window.
"""
from __future__ import annotations

from collections import deque


class AdaptiveDifficulty:
    def __init__(self):
        self.reset()

    def reset(self):
        self.outcomes = deque(maxlen=8)
        self.level = 0
        self._since_update = 0

    def record(self, hit: bool) -> bool:
        self.outcomes.append(bool(hit))
        self._since_update += 1
        # A minimum of three observations and one level per three clicks keeps
        # a single lucky hit/miss from causing an abrupt jump.
        if len(self.outcomes) < 3 or self._since_update < 3:
            return False
        self._since_update = 0
        accuracy = sum(self.outcomes) / len(self.outcomes)
        previous = self.level
        if accuracy >= .80:
            self.level = min(6, self.level + 1)
        elif accuracy <= .50:
            self.level = max(-4, self.level - 1)
        return self.level != previous

    @property
    def radius(self) -> float:
        return max(10.0, min(26.0, 18.0 - self.level * 1.5))

    @property
    def speed(self) -> float:
        return max(10.0, min(120.0, 40.0 + self.level * 12.0))

    @property
    def accuracy(self) -> float | None:
        return sum(self.outcomes) / len(self.outcomes) if self.outcomes else None
