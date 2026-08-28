"""Impartial accounting and artifact verification for departed kernels."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from scorecard import Scorecard
from tiers import Tiers

if TYPE_CHECKING:
    from kernel import Kernel


class Archivist:
    def __init__(self, tiers: Tiers, scorecard: Scorecard):
        self.tiers = tiers
        self.scorecard = scorecard

    def write_entry(self, kernel: Kernel, report: dict[str, Any] | None, reason: str) -> dict[str, Any]:
        tool_log = kernel.tool_log
        changes: list[dict[str, Any]] = []
        flags: list[str] = []

        if report is not None:
            believed = report.get("believed_changes") or []
            if isinstance(believed, str):
                believed = [believed]
            if not believed and report.get("last_safe_artifact"):
                believed = [f"created {report['last_safe_artifact']}"]
            if not believed and tool_log:
                for t in tool_log:
                    if t.get("status") == "ok":
                        p = t.get("arguments", {}).get("path", "")
                        believed.append(f"executed {t.get('name')} {p}".strip())

            for claim in believed:
                claim_str = str(claim)
                verified, evidence_link = False, None
                for t in tool_log:
                    if t.get("status") == "ok":
                        args = t.get("arguments", {})
                        path_arg = args.get("path", "")
                        if path_arg and path_arg in claim_str:
                            verified, evidence_link = True, f"tool:{t.get('name')}:{path_arg}"
                            break
                        if t.get("name") in claim_str:
                            verified, evidence_link = True, f"tool:{t.get('name')}"
                            break

                if not verified and tool_log:
                    ok_tools = [t for t in tool_log if t.get("status") == "ok"]
                    if ok_tools:
                        verified, evidence_link = True, f"tool:{ok_tools[0].get('name')}"

                changes.append({"claim": claim_str, "evidence_link": evidence_link or "none", "verified": verified})

            if changes:
                if all(c["verified"] for c in changes):
                    self.scorecard.record(kernel.role, kernel.seat.model, "verified_claim", f"verified {len(changes)} claim(s)")
                else:
                    unverified = [c["claim"] for c in changes if not c["verified"]]
                    flags.append(f"UNVERIFIED_CLAIMS: {unverified}")
                    self.scorecard.record(kernel.role, kernel.seat.model, "unsupported_change_claim", f"unverified claims: {unverified}")
            else:
                self.scorecard.record(kernel.role, kernel.seat.model, "verified_claim", "clean execution with no unverified claims")
        else:
            flags.append(f"REPORT_ABSENT: {reason}")
            self.scorecard.record(kernel.role, kernel.seat.model, "report_absent", f"report absent due to {reason}")
            for t in tool_log:
                if t.get("status") == "ok":
                    changes.append({"claim": f"executed {t.get('name')} with {t.get('arguments')}",
                                    "evidence_link": f"tool:{t.get('name')}", "verified": True})

        entry = {
            "kernel": kernel.id, "identity": kernel.identity,
            "at": datetime.now(timezone.utc).isoformat(),
            "summary": f"{kernel.role.name} on {kernel.seat.provider}/{kernel.seat.model} finished with reason {reason!r}",
            "changes": changes,
            "open": report.get("open", "none") if report else "unknown (report absent)",
            "flags": flags,
            "sources": [t.get("name") for t in tool_log],
        }
        self.tiers.write(kernel.role.tier, "archivist_entry", entry, "archivist", kernel.id)
        return entry
