"""One model context for a while, followed by two honest accounts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from harness import ChatHarness, Tool, TurnResult
from ledger import CACHE_TABLE, Ledger, Seat
from memory import Tiers
from roles import Role


REPORT_PROMPT = """You're being visited one last time. No tools. Return only a small JSON object:
{"intent":"...","believed_changes":["path or concrete change"],"why":"...",
 "open":["..."],"last_safe_artifact":"...","advice":"..."}
Say what you believe, briefly. An archivist will verify it separately."""


class KernelRegistry:
    def __init__(self):
        self.kernels: dict[str, Kernel] = {}

    def add(self, kernel: "Kernel") -> None:
        self.kernels[kernel.id] = kernel

    def remove(self, kernel: "Kernel") -> None:
        self.kernels.pop(kernel.id, None)

    def warm_for(self, role: str, conversation: str) -> "Kernel | None":
        for kernel in self.kernels.values():
            if kernel.role.name == role and kernel.conversation_id == conversation and kernel.is_warm():
                return kernel
        return None


class Archivist:
    def __init__(self, tiers: Tiers, scorecard):
        self.tiers, self.scorecard = tiers, scorecard
        self.entries: list[dict[str, Any]] = []

    def write_entry(self, kernel: "Kernel", report: dict[str, Any] | None, reason: str) -> dict[str, Any]:
        made = {str(item.get("result", {}).get("path", "")) for item in kernel.tool_log
                if item.get("ok") and item.get("name") == "write_file"}
        claims = report.get("believed_changes", []) if report else []
        checked = []
        for claim in claims if isinstance(claims, list) else [claims]:
            text = str(claim)
            folded = text.casefold().replace("\\", "/")
            evidence = next((path for path in made if path and
                             path.casefold().replace("\\", "/").split("/workspace/", 1)[-1] in folded), None)
            checked.append({"claim": text, "verified": bool(evidence), "evidence": evidence})
            event = "verified_change_claim" if evidence else "unsupported_change_claim"
            self.scorecard.record(kernel.role, kernel.seat.model, event, text)
        flags = [] if report else [f"REPORT_ABSENT: {reason}"]
        summary = ("Report absent; tool log is the only account." if not report else
                   f"Kernel reported {len(checked)} change(s); {sum(c['verified'] for c in checked)} verified.")
        entry = {"summary": summary, "reason": reason, "changes": checked, "flags": flags,
                 "sources": {"tool_log": kernel.tool_log, "transcript_messages": len(kernel.messages)},
                 "identity": list(kernel.identity)}
        self.tiers.write(kernel.role.tier, "archivist_entry", entry, "archivist", kernel.id)
        self.entries.append(entry)
        return entry


@dataclass
class DeathResult:
    report: dict[str, Any] | None
    entry: dict[str, Any]


class Kernel:
    def __init__(self, seat: Seat, role: Role, brief: str, effort: str,
                 conversation_id: str, harness: ChatHarness, ledger: Ledger,
                 tiers: Tiers, archivist: Archivist, registry: KernelRegistry):
        self.id = uuid4().hex[:12]
        self.seat, self.role, self.effort = seat, role, effort
        self.conversation_id = conversation_id
        self.harness, self.ledger, self.tiers = harness, ledger, tiers
        self.archivist, self.registry = archivist, registry
        session = conversation_id or self.id
        brief_hash = hashlib.sha256(brief.encode()).hexdigest()[:16]
        self.identity = (seat.provider, seat.endpoint, seat.model, seat.cache_boundary, session, brief_hash)
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": brief}]
        self.tool_log: list[dict[str, Any]] = []
        self.prefix_tokens, self.state = max(1, len(brief) // 4), "alive"
        self.last_turn_at = datetime.now(timezone.utc)
        self.cache_expires_at = self.last_turn_at + timedelta(seconds=seat.cache_window)
        registry.add(self)

    def is_warm(self) -> bool:
        return self.state == "alive" and self.seat.cache_window > 0 and datetime.now(timezone.utc) < self.cache_expires_at

    def turn(self, prompt: str, tools: dict[str, Tool] | None = None,
             max_tokens: int = 700, _report: bool = False) -> TurnResult:
        if self.state != "alive" and not (_report and self.state == "dying"):
            raise RuntimeError("kernel is not accepting turns")
        for name in (tools or {}):
            self.role.enforce("tools", name)
        result = self.harness.turn(self.seat, self.messages, prompt, tools, self.tool_log, max_tokens)
        self.prefix_tokens += result.tokens
        self.last_turn_at = datetime.now(timezone.utc)
        self.cache_expires_at = self.last_turn_at + timedelta(seconds=self.seat.cache_window)
        return result

    def die(self, reason: str, report_tokens: int = 350) -> DeathResult:
        if self.state == "dead":
            raise RuntimeError("kernel already died")
        self.state = "dying"
        report = None
        if self.ledger.reserve(self.seat, report_tokens):
            try:
                result = self.turn(REPORT_PROMPT, tools={}, max_tokens=report_tokens, _report=True)
                report = _json_object(result.text)
            except Exception:
                report = None
        if report:
            report["identity"] = list(self.identity)
            self.tiers.write(self.role.tier, "kernel_self_report", report, self.role.name, self.id)
        else:
            self.tiers.write(self.role.tier, "self_report_absence", {"reason": reason},
                             self.role.name, self.id)
        self.state = "dead"
        self.registry.remove(self)
        return DeathResult(report, self.archivist.write_entry(self, report, reason))


class Runtime:
    def __init__(self, harness: ChatHarness, ledger: Ledger, tiers: Tiers,
                 archivist: Archivist, registry: KernelRegistry):
        self.harness, self.ledger, self.tiers = harness, ledger, tiers
        self.archivist, self.registry = archivist, registry

    def spawn(self, seat: Seat, role: Role, brief: str, effort: str, convo=None) -> Kernel:
        return Kernel(seat, role, brief, effort, getattr(convo, "id", "") or uuid4().hex[:8],
                      self.harness, self.ledger, self.tiers, self.archivist, self.registry)


def _json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(cleaned[start:end + 1])
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                pass
        return None
