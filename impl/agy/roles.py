"""roles.py - role definitions formulated as denial sets."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Role:
    name: str
    denied_tools: frozenset[str] = field(default_factory=frozenset)
    denied_channels: frozenset[str] = field(default_factory=frozenset)
    denied_paths: frozenset[str] = field(default_factory=frozenset)
    denied_names: frozenset[str] = field(default_factory=frozenset)
    denied_hosts: frozenset[str] = field(default_factory=frozenset)
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    description: str = ""

    def can_use_tool(self, tool_name: str) -> bool:
        if tool_name in self.denied_tools:
            return False
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False
        return True

    def can_chat_in_channel(self, channel: str) -> bool:
        return channel not in self.denied_channels

    def is_path_denied(self, path: str) -> bool:
        return any(denied in path for denied in self.denied_paths)

    def is_name_denied(self, name: str) -> bool:
        return any(denied.lower() == name.lower() for denied in self.denied_names)


ROLES: dict[str, Role] = {
    "voice": Role(
        name="voice",
        denied_tools=frozenset({"write_file", "exec_shell", "delete_file"}),
        denied_channels=frozenset({"eng-internal", "prod-secrets"}),
        denied_paths=frozenset({"/etc", "/var", "/root", "credentials.json"}),
        denied_names=frozenset({"root_admin", "db_super"}),
        denied_hosts=frozenset({"internal.corp", "169.254.169.254"}),
        allowed_tools=frozenset({"redphone_handoff", "read_file", "post_message"}),
        description="Public voice of the house; communicates and routes work via redphone.",
    ),
    "builder": Role(
        name="builder",
        denied_tools=frozenset({"dm_user", "publish_public", "delete_database"}),
        denied_channels=frozenset({"dm-asa", "public-announcements"}),
        denied_paths=frozenset({"/etc/shadow", "/root", ".env", "secrets/"}),
        denied_names=frozenset({"payment_gateway_master"}),
        denied_hosts=frozenset({"api.billing.internal"}),
        allowed_tools=frozenset({"write_file", "read_file", "list_dir", "verify_syntax"}),
        description="Builder role that constructs artifacts in the workspace.",
    ),
    "archivist": Role(
        name="archivist",
        denied_tools=frozenset({"write_file", "exec_shell", "dm_user"}),
        denied_channels=frozenset({"dm-asa"}),
        denied_paths=frozenset({"secrets/"}),
        denied_names=frozenset(),
        denied_hosts=frozenset(),
        allowed_tools=frozenset({"read_file", "inspect_log", "verify_claims"}),
        description="Impartial auditor that validates kernel reports against tool logs.",
    ),
}
