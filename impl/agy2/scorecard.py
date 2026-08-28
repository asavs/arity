"""scorecard.py - Aptitude ranking, model trial records, and standing penalties."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from roles import Role


@dataclass
class TrialResult:
    role_name: str
    model_name: str
    score: float
    verified: bool
    at: float = field(default_factory=lambda: 0.0)


class Scorecard:
    """Tracks historical model performance and standing per role."""

    def __init__(self) -> None:
        # (role_name, model_name) -> standing multiplier [0.0, 1.0]
        self.standing: dict[tuple[str, str], float] = {}
        self.trials: list[TrialResult] = []

    def penalize_standing(self, role_name: str, model_name: str, reason: str) -> float:
        """Standing goes DOWN when caught claiming unmade changes."""
        key = (role_name, model_name)
        curr = self.standing.get(key, 1.0)
        new_standing = max(0.2, curr * 0.7)
        self.standing[key] = new_standing
        return new_standing

    def recover_standing(self, role_name: str, model_name: str, delta: float = 0.05) -> float:
        key = (role_name, model_name)
        curr = self.standing.get(key, 1.0)
        new_standing = min(1.0, curr + delta)
        self.standing[key] = new_standing
        return new_standing

    def rank(self, role: Role, task_class: str | None = None) -> list[dict[str, Any]]:
        """Rank models by aptitude and standing."""
        base_models = [
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "nvidia/nemotron-3-nano-30b-a3b",
            "openai/gpt-4o-mini",
        ]
        results = []
        for m in base_models:
            factor = self.standing.get((role.name, m), 1.0)
            score = 1.0
            if "coding" in role.aptitude_wanted or "schema" in role.aptitude_wanted:
                if "gemini-3.6" in m or "gpt-4o" in m:
                    score += 0.5
            if "briefing" in role.aptitude_wanted or "conversation" in role.aptitude_wanted:
                if "flash" in m:
                    score += 0.3
            final_score = score * factor
            results.append({
                "model": m,
                "score": final_score,
                "standing": factor,
                "reason": f"aptitude={score:.1f}, standing={factor:.2f}",
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results
