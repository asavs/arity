"""arity scorecard — Model standing ledger, rankings, and trial evidence.

Axiom 3: The model behind a bot is chosen per prompt, on evidence.
Axiom 9: Standing goes DOWN when a model is caught claiming changes it never made.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .handlers import JsonlRecordStore
from .seams import RecordStore
from .types import StoreRecord


@dataclass
class ScorecardRecord:
    """A scored evaluation entry for a model on a role."""
    model: str
    role: str
    task_id: str
    verdict: str  # "success" | "discrepancy" | "failed" | "absent_report"
    score_delta: float
    standing_after: float
    details: str = ""
    timestamp: float = field(default_factory=time.time)


class Scorecard:
    """Tracks empirical model standing by role, rewarding verified tasks and penalizing hallucinations."""

    def __init__(self, store: Optional[RecordStore] = None):
        self.store = store or JsonlRecordStore()
        self._standings: dict[str, float] = {}  # key: "role:model" -> standing
        self._load_from_store()

    def _key(self, role: str, model: str) -> str:
        return f"{role.lower()}:{model.lower()}"

    def get_standing(self, role: str, model: str) -> float:
        """Get the current accumulated standing for a role/model pair."""
        return self._standings.get(self._key(role, model), 10.0)  # Default base standing = 10.0

    def record_verdict(
        self,
        role: str,
        model: str,
        task_id: str,
        verdict: str,
        details: str = "",
    ) -> ScorecardRecord:
        """Update model standing based on archivist verdict."""
        if verdict == "success":
            delta = +1.0
        elif verdict == "discrepancy":
            # Severe penalty for hallucinating changes (Axiom 9)
            delta = -2.5
        elif verdict == "absent_report":
            delta = -1.0
        else:  # "failed"
            delta = -1.0

        key = self._key(role, model)
        current = self.get_standing(role, model)
        new_standing = max(0.0, current + delta)
        self._standings[key] = new_standing

        record = ScorecardRecord(
            model=model,
            role=role,
            task_id=task_id,
            verdict=verdict,
            score_delta=delta,
            standing_after=new_standing,
            details=details,
        )

        if self.store:
            try:
                self.store.append(
                    StoreRecord(
                        kind="scorecard",
                        record={
                            "role": role,
                            "model": model,
                            "task_id": task_id,
                            "verdict": verdict,
                            "score_delta": delta,
                            "standing_after": new_standing,
                            "details": details,
                            "timestamp": record.timestamp,
                        },
                    )
                )
            except Exception:
                pass

        return record

    def rank_models(self, role: str) -> list[tuple[str, float]]:
        """Return models ranked by standing for a given role (descending)."""
        prefix = f"{role.lower()}:"
        models = []
        for key, score in self._standings.items():
            if key.startswith(prefix):
                model_name = key[len(prefix):]
                models.append((model_name, score))
        return sorted(models, key=lambda x: x[1], reverse=True)

    def _load_from_store(self) -> None:
        """Replay past scorecard records to restore standing state."""
        if not hasattr(self.store, "query"):
            return
        records = self.store.query("scorecard")
        for rec in records:
            role = rec.get("role", "")
            model = rec.get("model", "")
            standing = rec.get("standing_after")
            if role and model and standing is not None:
                self._standings[self._key(role, model)] = float(standing)
