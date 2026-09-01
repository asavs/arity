"""Arity scorecard — model standings, rankings, and trial evidence.

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
        self._observations: dict[str, int] = {}  # same keys -> verdicts that moved them
        self._load_from_store()

    def _key(self, role: str, model: str) -> str:
        return f"{role.lower()}:{model.lower()}"

    def _derived_keys(
        self,
        role: str,
        model: str,
        signature: Optional[str] = None,
        harness: Optional[str] = None,
        tool_runner: Optional[str] = None,
        skills: Optional[list[Any]] = None,
    ) -> list[str]:
        """Every standing key one verdict moves, role:model first.

        Live updates and store replay both derive their keys here so the two can never
        disagree about which axes a verdict scored.
        """
        keys = [self._key(role, model)]
        if signature:
            keys.append(signature.lower())
        if harness:
            keys.append(f"harness:{harness.lower()}:{model.lower()}")
        if tool_runner:
            keys.append(f"tools:{tool_runner.lower()}:{model.lower()}")
        for sk in skills or []:
            sk_name = sk.lower() if isinstance(sk, str) else getattr(sk, "name", str(sk)).lower()
            keys.append(self._key(f"skill:{sk_name}", model))
        return keys

    def _apply_delta(self, keys: list[str], delta: float) -> float:
        """Move every derived key by `delta`, clamped at 0; return the role:model standing.

        Counting here and nowhere else is what makes replay reconstruct counts for free: live
        updates and store replay both arrive through this one helper.
        """
        for key in keys:
            self._standings[key] = max(0.0, self._standings.get(key, 10.0) + delta)
            self._observations[key] = self._observations.get(key, 0) + 1
        return self._standings[keys[0]]

    def get_standing(self, role_or_key: str, model: Optional[str] = None) -> float:
        """Get the accumulated standing for a role/model pair or a multidimensional signature key."""
        if model is None:
            # Direct key lookup (e.g. 'builder:gemini-3.6:wire:ast_tools' or 'gemini-3.6')
            return self._standings.get(role_or_key.lower(), 10.0)
        return self._standings.get(self._key(_role_key(role_or_key), model), 10.0)

    def get_observations(self, role_or_key: str, model: Optional[str] = None) -> int:
        """How many verdicts moved this standing. 0 means the value is the 10.0 baseline, not evidence."""
        if model is None:
            return self._observations.get(role_or_key.lower(), 0)
        return self._observations.get(self._key(_role_key(role_or_key), model), 0)

    def least_observed(self, keys: list[str]) -> Optional[str]:
        """The key with the fewest observations; ties broken by sorted key order, never dict order."""
        if not keys:
            return None
        return min(sorted(keys), key=self.get_observations)

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

        # Score every axis this verdict touches: role:model, the multidimensional signature,
        # harness:<h>:<model>, tools:<t>:<model>, and skill:<s>:<model>.
        new_standing = self._apply_delta(
            self._derived_keys(role, model, signature, harness, tool_runner, skills), delta
        )

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
        """Replay past scorecard records to restore standing state.

        Deltas are replayed rather than the persisted `standing_after` copied, because only the
        role:model standing is persisted: copying it onto the signature key attributes a
        role:model aggregate to one signature, and the harness/tools/skill axes have no
        persisted absolute at all. Records must arrive in append order, since the clamp at 0
        makes replay order-sensitive exactly as live updates are.
        """
        if not hasattr(self.store, "query"):
            return
        for rec in self.store.query("scorecard"):
            role = rec.get("role", "")
            model = rec.get("model", "")
            if not role or not model:
                continue
            delta = rec.get("score_delta")
            if delta is None:
                # Legacy or foreign record: the persisted absolute is the only evidence there is.
                # It is still exactly one verdict, so role:model counts it — but the derived keys
                # it would have moved are unreconstructable, and inventing counts for them would
                # claim evidence that was never persisted.
                standing = rec.get("standing_after")
                if standing is not None:
                    key = self._key(role, model)
                    self._standings[key] = float(standing)
                    self._observations[key] = self._observations.get(key, 0) + 1
                continue
            self._apply_delta(
                self._derived_keys(
                    role,
                    model,
                    rec.get("signature"),
                    rec.get("harness"),
                    rec.get("tool_runner"),
                    rec.get("skills"),
                ),
                float(delta),
            )
