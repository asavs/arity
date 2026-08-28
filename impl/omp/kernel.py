"""One model context for a while, followed by two honest accounts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from harness import ChatHarness, Tool, TurnResult
from ledger import Ledger, Seat
from roles import Role
from tiers import Tiers

if TYPE_CHECKING:
    from archivist import Archivist
    from cadence import Conversation

REPORT_PROMPT = """You're being visited one last time. No tools. Return only a small JSON object:
{"intent": "...", "believed_changes": ["..."], "why": "...", "open": "...", "last_safe_artifact": "...", "advice": "..."}
Say what you believe, briefly. An archivist will verify it separately."""


class KernelRegistry:
    def __init__(self) -> None:
        self.kernels: dict[str, Kernel] = {}
        self.holders: dict[tuple[str, str], Kernel] = {}

    def add(self, k: Kernel, role_name: str, convo_id: str | None = None) -> None:
        self.kernels[k.id] = k
        if convo_id:
            self.holders[(role_name, convo_id)] = k

    def warm_for(self, role_name: str, convo_id: str) -> Kernel | None:
        k = self.holders.get((role_name, convo_id))
        return k if k and k.is_warm() else None

    def remove(self, k: Kernel) -> None:
        self.kernels.pop(k.id, None)
        for pair in [p for p, h in self.holders.items() if h.id == k.id]:
            self.holders.pop(pair, None)


@dataclass
class DeathResult:
    report: dict[str, Any] | None
    entry: dict[str, Any]


class Kernel:
    def __init__(self, seat: Seat, role: Role, brief: str, effort: str,
                 convo: Conversation | None, harness: ChatHarness, ledger: Ledger,
                 tiers: Tiers, archivist: Archivist, registry: KernelRegistry) -> None:
        self.id = uuid4().hex[:12]
        self.seat, self.role, self.brief, self.effort = seat, role, brief, effort
        self.convo, self.harness, self.ledger, self.tiers = convo, harness, ledger, tiers
        self.archivist, self.registry = archivist, registry

        session_key = convo.id if convo else self.id
        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()[:16]
        self.identity = (seat.provider, seat.endpoint, seat.model, seat.cache_boundary, session_key, brief_hash)

        now = datetime.now(timezone.utc)
        self.born_at = self.last_turn_at = now
        self.cache_expires_at = now + timedelta(seconds=seat.cache_window)
        self.state = "alive"
        self.prefix_tokens = 0
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": brief}]
        self.tool_log: list[dict[str, Any]] = []
        self.transcript: list[dict[str, Any]] = []
        self.registry.add(self, role.name, convo.id if convo else None)

    def is_warm(self) -> bool:
        return datetime.now(timezone.utc) <= self.cache_expires_at and self.state == "alive"

    def turn(self, prompt: str, tools: dict[str, Tool] | None = None, max_tokens: int = 700) -> TurnResult:
        if self.state not in ("alive", "dying"):
            raise RuntimeError(f"kernel {self.id} is {self.state}, cannot execute turns")
        if tools:
            for tool_name in tools:
                self.role.enforce("tools", tool_name)

        result = self.harness.turn(self.seat, self.messages, prompt, tools, self.tool_log, max_tokens, self.effort)
        self.last_turn_at = datetime.now(timezone.utc)
        self.cache_expires_at = self.last_turn_at + timedelta(seconds=self.seat.cache_window)
        self.prefix_tokens += result.tokens
        self.transcript.extend([{"role": "user", "content": prompt}, {"role": "assistant", "content": result.text}])
        return result

    def write_report(self, reason: str) -> dict[str, Any] | None:
        if not self.ledger.reserve(self.seat, 150):
            return None
        try:
            res = self.turn(REPORT_PROMPT, tools={}, max_tokens=250)
            return _extract_json(res.text)
        except Exception:
            return None

    def trace(self) -> dict[str, Any]:
        return {"kernel_id": self.id, "identity": self.identity, "role": self.role.name,
                "seat": self.seat.id, "transcript": list(self.transcript), "tool_log": list(self.tool_log),
                "born_at": self.born_at.isoformat(), "last_turn_at": self.last_turn_at.isoformat()}

    def die(self, reason: str) -> DeathResult:
        self.state = "dying"
        report = self.write_report(reason)
        self.registry.remove(self)
        self.state = "dead"

        if report:
            self.tiers.write(self.role.tier, "kernel_self_report", report, "kernel", self.id)
        else:
            self.tiers.write(self.role.tier, "self_report_absence",
                             {"reason": reason, "flags": [f"REPORT_ABSENT: {reason}"]}, "kernel", self.id)

        entry = self.archivist.write_entry(self, report, reason)
        return DeathResult(report, entry)


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s != -1 and e > s:
            try:
                data = json.loads(cleaned[s : e + 1])
                return data if isinstance(data, dict) else None
            except Exception:
                pass
        return None
