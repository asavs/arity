"""gorkbot scorecard — Model standing ledger, rankings, and trial evidence.

Axiom 3: The model behind a bot is chosen per prompt, on evidence.
Axiom 9: Standing goes DOWN when a model is caught claiming changes it never made.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .handlers import JsonlRecordStore, default_record_store
from .seams import RecordStore
from .types import StoreRecord


_NAMESPACES = ("skill", "harness", "tools", "judge")


def _role_key(role: str) -> str:
    """developer:python -> developer.python so keys stay colon-separated; skill:/harness:/tools: are namespaces, untouched."""
    if ":" in role and role.split(":", 1)[0] not in _NAMESPACES:
        return role.replace(":", ".")
    return role


@dataclass
class ScorecardRecord:
    """A scored evaluation entry for a model, role, or multi-dimensional combination."""
    model: str
    role: str
    task_id: str
    verdict: str  # "success" | "discrepancy" | "failed" | "absent_report"
    score_delta: float
    standing_after: float
    details: str = ""
    signature: Optional[str] = None
    harness: Optional[str] = None
    tool_runner: Optional[str] = None
    skills: Optional[list[str]] = None
    timestamp: float = field(default_factory=time.time)

class Scorecard:
    """Tracks empirical model standing by role, rewarding verified tasks and penalizing hallucinations."""

    def __init__(self, store: Optional[RecordStore] = None):
        self.store = store or default_record_store()
        self._standings: dict[str, float] = {}  # key: "role:model" -> standing
        self._load_from_store()

    def _key(self, role: str, model: str) -> str:
        return f"{role.lower()}:{model.lower()}"

    def get_standing(self, role_or_key: str, model: Optional[str] = None) -> float:
        """Get the accumulated standing for a role/model pair or a multidimensional signature key."""
        if model is None:
            # Direct key lookup (e.g. 'builder:gemini-3.6:wire:ast_tools' or 'gemini-3.6')
            return self._standings.get(role_or_key.lower(), 10.0)
        return self._standings.get(self._key(_role_key(role_or_key), model), 10.0)

    def record_verdict(
        self,
        role: str,
        model: str,
        task_id: str,
        verdict: str,
        details: str = "",
        skills: Optional[list[str]] = None,
        signature: Optional[str] = None,
        harness: Optional[str] = None,
        tool_runner: Optional[str] = None,
        score_override: Optional[float] = None,
    ) -> ScorecardRecord:
        """Update model and combination standing based on archivist verdict across all axes."""
        role = _role_key(role)
        if score_override is not None:
            delta = score_override
        elif verdict == "success":
            delta = +1.0
        elif verdict == "discrepancy":
            # Severe penalty for hallucinating changes (Axiom 9)
            delta = -2.5
        elif verdict == "absent_report":
            delta = -1.0
        else:  # "failed"
            delta = -1.0

        # 1. Update standard role:model key
        key = self._key(role, model)
        current = self.get_standing(role, model)
        new_standing = max(0.0, current + delta)
        self._standings[key] = new_standing

        # 2. Update multidimensional combination signature if provided
        sig_key = signature.lower() if signature else None
        if sig_key:
            sig_current = self._standings.get(sig_key, 10.0)
            self._standings[sig_key] = max(0.0, sig_current + delta)

        # 3. Update harness-specific standing (e.g. harness:wire:gemini-flash)
        if harness:
            h_key = f"harness:{harness.lower()}:{model.lower()}"
            h_current = self._standings.get(h_key, 10.0)
            self._standings[h_key] = max(0.0, h_current + delta)

        # 4. Update tool runner standing (e.g. tools:ast_tools:gemini-flash)
        if tool_runner:
            t_key = f"tools:{tool_runner.lower()}:{model.lower()}"
            t_current = self._standings.get(t_key, 10.0)
            self._standings[t_key] = max(0.0, t_current + delta)

        # 5. Update skill-specific standings (e.g. skill:pytest-tdd:gemini-flash)
        if skills:
            for sk in skills:
                sk_name = sk.lower() if isinstance(sk, str) else getattr(sk, "name", str(sk)).lower()
                sk_key = self._key(f"skill:{sk_name}", model)
                sk_current = self._standings.get(sk_key, 10.0)
                self._standings[sk_key] = max(0.0, sk_current + delta)

        record = ScorecardRecord(
            model=model,
            role=role,
            task_id=task_id,
            verdict=verdict,
            score_delta=delta,
            standing_after=new_standing,
            details=details,
            signature=signature,
            harness=harness,
            tool_runner=tool_runner,
            skills=skills,
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
                            "signature": signature,
                            "harness": harness,
                            "tool_runner": tool_runner,
                            "skills": skills,
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
            if key.startswith(prefix) and key.count(":") == 1:
                model_name = key[len(prefix):]
                models.append((model_name, score))
        return sorted(models, key=lambda x: x[1], reverse=True)

    def rank_combinations(self, role: Optional[str] = None) -> list[tuple[str, float]]:
        """Return multi-dimensional combination signatures ranked by standing (descending)."""
        combos = []
        for key, score in self._standings.items():
            # Combinations contain 3 or more segments (e.g. role:model:harness:tools)
            if key.count(":") >= 3:
                if role and not key.startswith(f"{role.lower()}:"):
                    continue
                combos.append((key, score))
        return sorted(combos, key=lambda x: x[1], reverse=True)

    def get_summary(self) -> str:
        """Return a formatted summary of top-rated models and combinations."""
        if not self._standings:
            return "No historical ratings recorded yet (all models at baseline 10.0 pts)."
        lines = []
        # Show combinations first if present
        combos = self.rank_combinations()
        if combos:
            lines.append("### Multi-Dimensional Combination Standings")
            for k, v in combos[:5]:
                lines.append(f"• {k} -> {v:.1f} pts")
            lines.append("")
        lines.append("### Role & Model Standings")
        standard_entries = [(k, v) for k, v in self._standings.items() if k.count(":") < 3]
        for k, v in sorted(standard_entries, key=lambda x: x[1], reverse=True)[:6]:
            lines.append(f"• {k}: {v:.1f} pts")
        return "\n".join(lines)

    def _load_from_store(self) -> None:
        """Replay past scorecard records to restore standing state."""
        if not hasattr(self.store, "query"):
            return
        records = self.store.query("scorecard")
        for rec in records:
            role = rec.get("role", "")
            model = rec.get("model", "")
            standing = rec.get("standing_after")
            signature = rec.get("signature")
            if role and model and standing is not None:
                self._standings[self._key(role, model)] = float(standing)
            if signature and standing is not None:
                self._standings[signature.lower()] = float(standing)
