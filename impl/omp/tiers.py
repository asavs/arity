"""Compile only the memory a role may carry, then keep both accounts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from roles import BriefLeak, Role
from store import Store


@dataclass
class Task:
    id: str
    want: str
    project: str = "general"
    stakes: str = "low"
    size: int = 300
    context: dict[str, Any] | None = None


class Tiers:
    def __init__(self, store: Store):
        self.store = store
        self.universal = [
            "You are one temporary kernel holding a persistent role.",
            "You will be visited. You are never told when this kernel ends.",
            "Use tools for claims about changed files; do not invent tool results.",
        ]
        self.memory = {0: ["The front voice protects continuity and delegates deep work."],
                       1: ["Project work is handed off as bounded records."], 2: []}

    def assemble(self, role: Role, task: Task, predecessor: dict | None = None) -> str:
        payload: dict[str, Any] = {"facts": self.universal, "memory": [], "task": asdict(task)}
        for tier in range(role.tier, 3):
            payload["memory"].extend(self.memory.get(tier, []))
        if predecessor:
            payload["predecessor"] = predecessor

        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        for kind in ("paths", "names", "hosts"):
            for needle in getattr(role.deny, kind):
                if needle.casefold() in rendered.casefold():
                    raise BriefLeak(f"brief refused: denied {kind[:-1]} leaked: {needle}")
        return rendered

    def write(self, tier: int, kind: str, body: Any, by: str, kernel: str) -> None:
        self.store.append(f"state/tier-{tier}.jsonl", {
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind, "by": by, "kernel": kernel, "body": body,
        })

    def retrieve(self, tier: int, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.read(f"state/tier-{tier}.jsonl")
        return [r for r in rows if r.get("kind") == kind] if kind else rows
