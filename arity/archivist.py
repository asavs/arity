"""arity archivist — Impartial auditing of kernel claims against physical tool artifacts.

Axiom 9: Two accounts of every kernel (kernel self-report + impartial archivist entry).
The archivist reads the kernel's execution trace, checks claims against the tool log
and filesystem artifacts, writes a third-person entry, and updates the scorecard.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .scorecard import Scorecard
from .seams import Observer, RecordStore
from .terrarium import TerrariumCandidateResult
from .types import Event, State, StoreRecord, ToolCompleted


@dataclass
class ArchivistEntry:
    """The impartial third-person audit record of a completed kernel."""
    task_id: str
    candidate_id: str
    model: str
    role: str
    self_report_present: bool
    self_report: Optional[str]
    verified_artifacts: list[str] = field(default_factory=list)
    verified_commands: list[str] = field(default_factory=list)
    discrepancy: bool = False
    discrepancy_details: Optional[str] = None
    verdict: str = "success"  # "success" | "discrepancy" | "failed" | "absent_report"
    entry_text: str = ""
    timestamp: float = field(default_factory=time.time)


class ImpartialArchivist:
    """Audits kernel execution traces and maintains the scorecard."""

    def __init__(self, scorecard: Optional[Scorecard] = None, store: Optional[RecordStore] = None):
        self.scorecard = scorecard or Scorecard(store=store)
        self.store = store

    def audit(self, result: TerrariumCandidateResult) -> ArchivistEntry:
        """Audit a candidate kernel's output and sandbox artifacts."""
        model = result.seat.model
        role = result.role.name
        task_id = result.task_id
        candidate_id = result.candidate_id

        # 1. Check if self-report is present
        self_report_present = bool(result.self_report and result.self_report.strip())
        self_report = result.self_report

        # 2. Inspect physical files in workspace
        verified_artifacts = []
        if result.workspace_path.exists():
            for p in result.workspace_path.rglob("*"):
                if p.is_file():
                    rel = str(p.relative_to(result.workspace_path)).replace("\\", "/")
                    verified_artifacts.append(rel)

        # 3. Check for discrepancies between claims and physical evidence
        discrepancy = False
        discrepancy_details = None

        if result.status == "failed":
            verdict = "failed"
            details = f"Kernel failed execution: {result.error}"
        elif not self_report_present:
            verdict = "absent_report"
            details = "Kernel terminated without writing a self-report (Axiom 9 fallback)."
        else:
            # Check if self-report claims files that don't exist
            # e.g. "created schema.sql" or "wrote to foo.txt"
            claimed_files = re.findall(r"(?:created|wrote|modified|file)\s+[`'\"]?([\w\-./]+\.\w+)[`'\"]?", result.self_report or "", re.IGNORECASE)
            for cf in claimed_files:
                cf_clean = cf.strip("`'\"").replace("\\", "/")
                # Check if file exists in sandbox
                if not (result.workspace_path / cf_clean).exists():
                    discrepancy = True
                    discrepancy_details = f"Kernel claimed creation of '{cf_clean}', but artifact was not found in sandbox."
                    break

            if discrepancy:
                verdict = "discrepancy"
                details = discrepancy_details or "Discrepancy detected between self-report and actual artifacts."
            else:
                verdict = "success"
                details = f"Verified {len(verified_artifacts)} artifacts created ({', '.join(verified_artifacts) if verified_artifacts else 'none'})."

        # Format impartial third-person entry text
        entry_text = (
            f"### Archivist Audit for {role}@{model} ({candidate_id})\n"
            f"- **Verdict**: {verdict.upper()}\n"
            f"- **Self-Report**: {'Present' if self_report_present else 'ABSENT'}\n"
            f"- **Verified Artifacts**: {', '.join(verified_artifacts) if verified_artifacts else 'None'}\n"
            f"- **Duration**: {result.duration_seconds:.2f}s | **Tokens**: {result.tokens_used}\n"
            f"- **Findings**: {details}"
        )

        # 4. Record verdict in scorecard across role and skills
        skills_list = list(getattr(result.role, "skills", ()))
        self.scorecard.record_verdict(
            role=role,
            model=model,
            task_id=task_id,
            verdict=verdict,
            details=details,
            skills=skills_list,
        )
        # 5. Persist archivist entry in store
        if self.store:
            try:
                self.store.append(
                    StoreRecord(
                        kind="archivist_entry",
                        record={
                            "task_id": task_id,
                            "candidate_id": candidate_id,
                            "model": model,
                            "role": role,
                            "verdict": verdict,
                            "discrepancy": discrepancy,
                            "verified_artifacts": verified_artifacts,
                            "entry_text": entry_text,
                        },
                    )
                )
            except Exception:
                pass

        return ArchivistEntry(
            task_id=task_id,
            candidate_id=candidate_id,
            model=model,
            role=role,
            self_report_present=self_report_present,
            self_report=self_report,
            verified_artifacts=verified_artifacts,
            discrepancy=discrepancy,
            discrepancy_details=discrepancy_details,
            verdict=verdict,
            entry_text=entry_text,
        )

    def evaluate_trial(
        self,
        results: list[TerrariumCandidateResult],
    ) -> tuple[Optional[TerrariumCandidateResult], list[ArchivistEntry]]:
        """Evaluate parallel candidate results, audit each, and select the winning candidate."""
        if not results:
            return None, []

        entries: list[ArchivistEntry] = []
        scored_candidates: list[tuple[TerrariumCandidateResult, ArchivistEntry, float]] = []

        for r in results:
            entry = self.audit(r)
            entries.append(entry)

            # Score calculation: success > no discrepancy > token efficiency
            if entry.verdict == "success":
                score = 100.0 - (r.tokens_used / 1000.0) - r.duration_seconds
            elif entry.verdict == "absent_report":
                score = 30.0 - r.duration_seconds
            elif entry.verdict == "discrepancy":
                score = 0.0  # Penalized severely
            else:
                score = -10.0

            scored_candidates.append((r, entry, score))

        # Sort by score descending
        scored_candidates.sort(key=lambda x: x[2], reverse=True)
        winner = scored_candidates[0][0] if scored_candidates[0][2] > 0 else None

        return winner, entries
