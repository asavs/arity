"""cadence.py - inter-arrival tracking and Bayesian return probability."""

from __future__ import annotations
import math
import time
from dataclasses import dataclass, field


@dataclass
class CadenceTracker:
    last_interaction: dict[str, float] = field(default_factory=dict)
    history: dict[str, list[float]] = field(default_factory=dict)
    default_decay_rate: float = 0.05

    def record_interaction(self, session_id: str, timestamp: float | None = None) -> float:
        now = timestamp if timestamp is not None else time.time()
        dt = 0.0
        if session_id in self.last_interaction:
            dt = max(0.0, now - self.last_interaction[session_id])
            self.history.setdefault(session_id, []).append(dt)
        self.last_interaction[session_id] = now
        return dt

    def p_return(self, session_id: str, elapsed: float | None = None) -> float:
        """Compute p(return) based on elapsed time since last interaction."""
        if session_id not in self.last_interaction:
            return 0.50

        if elapsed is None:
            elapsed = max(0.0, time.time() - self.last_interaction[session_id])

        intervals = self.history.get(session_id, [])
        if intervals:
            avg_interval = sum(intervals) / len(intervals)
            decay_rate = 1.0 / max(1.0, avg_interval)
        else:
            decay_rate = self.default_decay_rate

        prob = math.exp(-decay_rate * elapsed)
        return max(0.01, min(0.99, prob))
