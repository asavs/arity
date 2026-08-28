"""Inter-message gap prediction and arrival probability."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Conversation:
    id: str
    kind: str = "dm"
    recent_gaps: list[float] = field(default_factory=list)
    last_at: datetime | None = None


class Cadence:
    priors = {"call": 5.0, "dm": 1500.0, "project": 7200.0}

    def predict(self, convo: Conversation | None, horizon_s: float | None = None) -> dict[str, float]:
        if convo is None:
            return {"p50": 0.0, "p_return": 0.5}
        gaps = convo.recent_gaps[-8:] or [self.priors.get(convo.kind, 1500.0)]
        p50 = float(statistics.median(gaps))
        horizon = horizon_s if horizon_s is not None else p50
        empirical = sum(gap <= horizon for gap in gaps) / len(gaps)
        smooth = 1.0 - math.exp(-horizon / max(p50, 1.0))
        return {"p50": p50, "p_return": min(1.0, max(0.0, 0.8 * empirical + 0.2 * smooth))}
