"""archivist.py - Impartial account writer, verifying claims against tool logs and disk."""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any
from kernel import EvidenceEnvelope
from store import Store, Record
from scorecard import Scorecard

VALID_EXTENSIONS = (".sql", ".py", ".txt", ".json", ".md", ".csv", ".html", ".sh", ".yaml", ".yml")


@dataclass
class ArchivistEntry:
    kernel_id: str
    summary: str
    verified_changes: list[dict[str, Any]]
    flags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)


class Archivist:
    """Impartial recorder that audits dead kernels and updates the scorecard."""

    def __init__(self, scorecard: Scorecard) -> None:
        self.scorecard = scorecard
        self.entries: list[ArchivistEntry] = []

    def enqueue(
        self,
        env: EvidenceEnvelope,
        report: str | None,
        reason: str,
        store: Store,
    ) -> ArchivistEntry:
        claims: list[str] = []
        flags: list[str] = []

        if not report:
            flags.append(f"REPORT_ABSENT: {reason}")
        else:
            # Extract claimed file actions
            for word in report.split():
                cleaned = word.strip("`'\",:;()[]{}*")
                if any(cleaned.endswith(ext) for ext in VALID_EXTENSIONS) or cleaned.startswith("brokie/") or cleaned.startswith("workspace/"):
                    if "." in cleaned or "/" in cleaned:
                        claims.append(cleaned)

        # Audit claims against tool log & disk
        verified_changes: list[dict[str, Any]] = []
        tool_writes = [
            t["args"].get("path") for t in env.tool_log if t.get("tool") == "write_file"
        ]

        if not claims and tool_writes:
            claims = [str(w) for w in tool_writes if w]

        for claim in sorted(set(claims)):
            exists_on_disk = store.file_exists(claim)
            logged_in_tool = claim in tool_writes
            is_verified = exists_on_disk and logged_in_tool
            verified_changes.append({
                "claim": f"write {claim}",
                "verified": is_verified,
                "on_disk": exists_on_disk,
                "in_tool_log": logged_in_tool,
            })

            if not is_verified and report:
                # Model claimed a change it did not make -> penalize standing
                flags.append(f"UNVERIFIED_CLAIM: {claim}")
                self.scorecard.penalize_standing(
                    role_name=env.role.name,
                    model_name=env.seat.model,
                    reason=f"Claimed write {claim} without tool proof",
                )

        summary = (
            f"Kernel {env.kernel_id} ({env.role.name} on {env.seat.model}) ended by '{reason}'. "
            f"Verified {sum(1 for v in verified_changes if v['verified'])}/{len(verified_changes)} changes."
        )

        entry = ArchivistEntry(
            kernel_id=env.kernel_id,
            summary=summary,
            verified_changes=verified_changes,
            flags=flags,
            sources=[f"tool_calls:{len(env.tool_log)}", f"tokens:{env.tokens_used}"],
        )
        self.entries.append(entry)

        store.write_record(
            tier=env.role.tier,
            record=Record(tier=env.role.tier, kind="archivist_entry", body=entry, by="archivist"),
        )
        return entry
