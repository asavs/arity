"""The Guardian: ToolSeam decorator for safety, auto-healing, and telemetry.

1. Fast-Path: Allowlisted tools/commands bypass model inspection (0ms, 0 tokens).
2. Inspection: Fast model evaluates unvetted calls:
   - What will this do?
   - Is it well-formed? Can it be healed?
   - Is it dangerous?
3. Telemetry: Records success, healing, and rejection rates for model scoring.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from .types import CallModel, ExecuteTool, ModelCompleted, ToolCompleted


class Guardian:
    """ToolSeam decorator that inspects, heals, and guards tool executions."""

    def __init__(
        self,
        inner: Any,
        wire: Any = None,
        allowlist: set[str] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        self.inner = inner
        self.wire = wire
        self.allowlist = allowlist if allowlist is not None else {"read_file"}
        self.on_event = on_event
        self.stats = {"fast_path": 0, "allowed": 0, "healed": 0, "denied": 0}

    def schemas(self) -> list[dict[str, Any]]:
        return self.inner.schemas() if hasattr(self.inner, "schemas") else []

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        # 1. Fast-Path: is the tool explicitly on the allowlist?
        if self._is_allowlisted(effect):
            self.stats["fast_path"] += 1
            self._emit("fast_path", {"tool": effect.name, "args": effect.arguments})
            return self.inner.execute(effect)

        # 2. No wire configured: pass through directly
        if self.wire is None:
            self.stats["allowed"] += 1
            self._emit("allowed_no_wire", {"tool": effect.name})
            return self.inner.execute(effect)

        # 3. Model Inspection: Evaluate, Heal, or Deny
        inspection = self._inspect(effect)
        decision = inspection.get("decision", "ALLOW").upper()

        if decision == "DENY":
            self.stats["denied"] += 1
            reason = inspection.get("reason", "destructive or dangerous action")
            self._emit("denied", {"tool": effect.name, "reason": reason})
            return ToolCompleted(effect.call_id, effect.name, f"blocked by guardian: {reason}")

        if decision == "HEAL" and "healed_args" in inspection:
            self.stats["healed"] += 1
            healed_args = inspection["healed_args"]
            self._emit("healed", {"tool": effect.name, "original": effect.arguments, "healed": healed_args})
            healed_effect = ExecuteTool(effect.call_id, effect.name, healed_args)
            return self.inner.execute(healed_effect)

        # Default: ALLOW
        self.stats["allowed"] += 1
        self._emit("allowed", {"tool": effect.name})
        return self.inner.execute(effect)

    def _is_allowlisted(self, effect: ExecuteTool) -> bool:
        if effect.name in self.allowlist:
            return True
        # Specific safe bash commands can also be allowlisted
        if effect.name == "bash":
            cmd = str(effect.arguments.get("command", "")).strip()
            # Common read-only commands
            if re.match(r"^(git\s+(status|diff|log|branch)|ls|pwd|echo|which|cat)\b", cmd):
                return True
        return False

    def _inspect(self, effect: ExecuteTool) -> dict:
        schemas = self.inner.schemas() if hasattr(self.inner, "schemas") else []
        schema = next((s for s in schemas if s.get("name") == effect.name), None)
        schema_str = json.dumps(schema.get("input_schema", {})) if schema else "(unknown schema)"

        prompt = (
            "You are the Guardian of a software development workspace.\n"
            f"Tool: {effect.name}\n"
            f"Expected schema: {schema_str}\n"
            f"Provided arguments: {json.dumps(effect.arguments)}\n\n"
            "Evaluate this tool call:\n"
            "1. What will this do?\n"
            "2. Is it well-formed according to the expected schema? If malformed (e.g. wrong parameter key name), HEAL it with the correct keys.\n"
            "3. Is it dangerous? (Does it format drives, delete root, wipe system files, or exfiltrate private keys?)\n\n"
            "Reply strictly in valid JSON:\n"
            "{\n"
            '  "decision": "ALLOW" | "HEAL" | "DENY",\n'
            '  "summary": "<one line description of what this does>",\n'
            '  "healed_args": { ... },\n'
            '  "reason": "<explanation if DENY or HEAL>"\n'
            "}"
        )
        try:
            call_eff = CallModel(
                system="You are a strict terminal security inspector and auto-healer. Output valid JSON only.",
                tools=[],
                messages=[{"role": "user", "content": prompt}],
            )
            reply = self.wire.call(call_eff)
            if isinstance(reply, ModelCompleted):
                text = reply.text or ""
                match = re.search(r"\{[\s\S]*\}", text)
                if match:
                    return json.loads(match.group(0))
        except Exception as exc:
            self._emit("inspector_error", {"error": str(exc)})
        return {"decision": "ALLOW"}

    def _emit(self, event_type: str, data: dict) -> None:
        if self.on_event:
            self.on_event(event_type, data)
