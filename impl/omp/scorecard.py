"""Model scorecard and evidence-based standing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from store import Store

if TYPE_CHECKING:
    from roles import Role


@dataclass
class ModelStanding:
    role: str
    model: str
    verified_claims: int = 0
    false_claims: int = 0
    absent_reports: int = 0
    standing_factor: float = 1.0
    history: list[dict[str, Any]] = field(default_factory=list)


class Scorecard:
    def __init__(self, store: Store):
        self.store = store
        self.standings: dict[tuple[str, str], ModelStanding] = {}
        for row in self.store.read("state/scorecard.jsonl"):
            st = self._get_or_create(row["role"], row["model"])
            st.standing_factor = float(row["standing_after"])
            ev = row.get("event")
            if ev == "unsupported_change_claim":
                st.false_claims += 1
            elif ev == "verified_claim":
                st.verified_claims += 1
            elif ev == "report_absent":
                st.absent_reports += 1

    def _get_or_create(self, role_name: str, model: str) -> ModelStanding:
        return self.standings.setdefault((role_name, model), ModelStanding(role_name, model))

    def rank(self, role: Role, available: list[str]) -> list[str]:
        wanted = list(dict.fromkeys(role.preferred_models + tuple(available)))
        cands = [m for m in wanted if m in available]
        return sorted(cands, key=lambda m: (-self._get_or_create(role.name, m).standing_factor, wanted.index(m)))

    def record(self, role: Role, model: str, event: str, detail: str) -> None:
        st = self._get_or_create(role.name, model)
        before = st.standing_factor
        if event == "unsupported_change_claim":
            st.false_claims += 1
            after = before * 0.75
        elif event == "verified_claim":
            st.verified_claims += 1
            after = min(1.0, before + 0.02)
        elif event == "report_absent":
            st.absent_reports += 1
            after = before
        else:
            after = min(1.0, before + 0.01)

        st.standing_factor = after
        rec = {
            "role": role.name, "model": model, "event": event, "detail": detail,
            "standing_before": before, "standing_after": after,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        st.history.append(rec)
        self.store.append("state/scorecard.jsonl", rec)
