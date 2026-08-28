"""scorecard.py - model standing and penalty tracking."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ModelStanding:
    provider: str
    model: str
    standing: float = 100.0
    verified_claims: int = 0
    false_claims: int = 0
    absent_reports: int = 0
    total_audits: int = 0
    penalties: list[dict[str, str | float]] = field(default_factory=list)


@dataclass
class Scorecard:
    records: dict[str, ModelStanding] = field(default_factory=dict)

    def _get_or_create(self, provider: str, model: str) -> ModelStanding:
        key = f"{provider}:{model}"
        if key not in self.records:
            self.records[key] = ModelStanding(provider=provider, model=model)
        return self.records[key]

    def record_verified(self, provider: str, model: str, details: str = "ok") -> None:
        rec = self._get_or_create(provider, model)
        rec.verified_claims += 1
        rec.total_audits += 1
        rec.standing = min(100.0, rec.standing + 1.0)

    def record_false_claim(
        self,
        provider: str,
        model: str,
        claimed: str,
        actual: str,
        penalty: float = 25.0,
    ) -> None:
        rec = self._get_or_create(provider, model)
        rec.false_claims += 1
        rec.total_audits += 1
        rec.standing = max(0.0, rec.standing - penalty)
        rec.penalties.append({
            "type": "FALSE_CLAIM",
            "penalty": penalty,
            "claimed": claimed,
            "actual": actual,
        })

    def record_absent_report(
        self,
        provider: str,
        model: str,
        reason: str = "missing report",
        penalty: float = 10.0,
    ) -> None:
        rec = self._get_or_create(provider, model)
        rec.absent_reports += 1
        rec.total_audits += 1
        rec.standing = max(0.0, rec.standing - penalty)
        rec.penalties.append({
            "type": "ABSENT_REPORT",
            "penalty": penalty,
            "reason": reason,
        })

    def get_standing(self, provider: str, model: str) -> float:
        key = f"{provider}:{model}"
        if key in self.records:
            return self.records[key].standing
        return 100.0
