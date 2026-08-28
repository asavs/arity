"""store.py - message channels, tool audit logs, and workspace filesystem."""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Store:
    workspace_dir: Path = field(default_factory=lambda: Path("./workspace"))
    channels: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    tool_logs: list[dict[str, Any]] = field(default_factory=list)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def post_message(
        self,
        channel: str,
        sender: str,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        msg = {
            "channel": channel,
            "sender": sender,
            "content": content,
            "meta": meta or {},
            "timestamp": time.time(),
        }
        self.channels.setdefault(channel, []).append(msg)
        return msg

    def get_messages(self, channel: str) -> list[dict[str, Any]]:
        return list(self.channels.get(channel, []))

    def log_tool(
        self,
        kernel_id: str,
        tool: str,
        args: dict[str, Any],
        result: str,
        files_written: list[str] | None = None,
        files_read: list[str] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "kernel_id": kernel_id,
            "tool": tool,
            "args": args,
            "result": result,
            "files_written": files_written or [],
            "files_read": files_read or [],
            "timestamp": time.time(),
        }
        self.tool_logs.append(entry)
        return entry

    def get_tool_logs(self, kernel_id: str) -> list[dict[str, Any]]:
        return [entry for entry in self.tool_logs if entry["kernel_id"] == kernel_id]

    def save_report(self, kernel_id: str, report: dict[str, Any]) -> None:
        self.reports[kernel_id] = report

    def get_report(self, kernel_id: str) -> dict[str, Any] | None:
        return self.reports.get(kernel_id)

    def write_workspace_file(self, rel_path: str, content: str) -> str:
        target = (self.workspace_dir / rel_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)

    def read_workspace_file(self, rel_path: str) -> str:
        target = (self.workspace_dir / rel_path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")
        return target.read_text(encoding="utf-8")
