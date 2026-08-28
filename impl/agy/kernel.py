"""kernel.py - kernel execution instance with identity tuple and self-reporting."""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from .roles import Role
from .tiers import Tier
from .store import Store
from .harness import Harness, TurnResult


@dataclass(frozen=True)
class KernelIdentity:
    provider: str
    endpoint: str
    model: str
    cache_boundary: str
    session: str
    brief_hash: str

    def as_tuple(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.provider,
            self.endpoint,
            self.model,
            self.cache_boundary,
            self.session,
            self.brief_hash,
        )


@dataclass
class KernelReport:
    kernel_id: str
    identity: tuple[str, str, str, str, str, str]
    role: str
    claimed_files_written: list[str] = field(default_factory=list)
    claimed_files_read: list[str] = field(default_factory=list)
    claimed_handoffs: list[str] = field(default_factory=list)
    summary: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kernel_id": self.kernel_id,
            "identity": list(self.identity),
            "role": self.role,
            "claimed_files_written": self.claimed_files_written,
            "claimed_files_read": self.claimed_files_read,
            "claimed_handoffs": self.claimed_handoffs,
            "summary": self.summary,
            "timestamp": self.timestamp,
        }


class Kernel:
    def __init__(
        self,
        kernel_id: str,
        identity: KernelIdentity,
        tier: Tier,
        store: Store,
        harness: Harness,
        seat_api_key: str,
        tools_spec: list[dict[str, Any]] | None = None,
        custom_tool_handlers: dict[str, Callable[[dict[str, Any]], str]] | None = None,
    ) -> None:
        self.kernel_id = kernel_id
        self.identity = identity
        self.tier = tier
        self.role: Role = tier.role
        self.store = store
        self.harness = harness
        self._seat_api_key = seat_api_key
        self.tools_spec = tools_spec or []
        self.custom_tool_handlers = custom_tool_handlers or {}
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": tier.brief}
        ]
        self.alive = True
        self.written_files: list[str] = []
        self.read_files: list[str] = []
        self.handoffs_issued: list[str] = []

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        if not self.role.can_use_tool(name):
            err = f"PermissionDenied: Role '{self.role.name}' denied access to tool '{name}'"
            self.store.log_tool(self.kernel_id, name, args, err)
            return err

        if name in self.custom_tool_handlers:
            res = self.custom_tool_handlers[name](args)
            files_w = [args["path"]] if "path" in args and "write" in name else []
            files_r = [args["path"]] if "path" in args and "read" in name else []
            self.store.log_tool(self.kernel_id, name, args, res, files_w, files_r)
            return res

        if name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            if self.role.is_path_denied(path):
                res = f"DeniedPathError: Path '{path}' is denied for role '{self.role.name}'"
                self.store.log_tool(self.kernel_id, name, args, res)
                return res
            full_p = self.store.write_workspace_file(path, content)
            self.written_files.append(path)
            res = f"Successfully wrote {len(content)} bytes to {path} ({full_p})"
            self.store.log_tool(self.kernel_id, name, args, res, files_written=[path])
            return res

        if name == "read_file":
            path = args.get("path", "")
            if self.role.is_path_denied(path):
                res = f"DeniedPathError: Path '{path}' is denied"
                self.store.log_tool(self.kernel_id, name, args, res)
                return res
            try:
                content = self.store.read_workspace_file(path)
                self.read_files.append(path)
                res = content
                self.store.log_tool(self.kernel_id, name, args, "read ok", files_read=[path])
            except Exception as e:
                res = f"Error reading file: {e}"
                self.store.log_tool(self.kernel_id, name, args, res)
            return res

        res = f"Tool '{name}' executed with args: {args}"
        self.store.log_tool(self.kernel_id, name, args, res)
        return res

    def step(self, user_prompt: str) -> TurnResult:
        if not self.alive:
            raise RuntimeError(f"Kernel {self.kernel_id} is dead.")
        self.messages.append({"role": "user", "content": user_prompt})
        turn = self.harness.run_turn(
            endpoint=self.identity.endpoint,
            api_key=self._seat_api_key,
            model=self.identity.model,
            messages=self.messages,
            tools_spec=self.tools_spec,
            tool_executor=self._execute_tool,
        )
        self.messages.append({"role": "assistant", "content": turn.content})
        return turn

    def file_report(
        self,
        claimed_written: list[str] | None = None,
        claimed_read: list[str] | None = None,
        summary: str = "",
    ) -> KernelReport:
        report = KernelReport(
            kernel_id=self.kernel_id,
            identity=self.identity.as_tuple(),
            role=self.role.name,
            claimed_files_written=claimed_written if claimed_written is not None else list(self.written_files),
            claimed_files_read=claimed_read if claimed_read is not None else list(self.read_files),
            claimed_handoffs=list(self.handoffs_issued),
            summary=summary or f"Kernel {self.kernel_id} completed role {self.role.name}",
        )
        self.store.save_report(self.kernel_id, report.to_dict())
        return report

    def die(self, reason: str = "completed", file_report: bool = True) -> None:
        if file_report and self.store.get_report(self.kernel_id) is None:
            self.file_report(summary=f"Dying on reason: {reason}")
        self.alive = False
