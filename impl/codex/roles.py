"""A role is mostly a crisp list of doors that stay shut."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class Denied(PermissionError):
    pass


@dataclass(frozen=True)
class Access:
    tools: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()
    hosts: frozenset[str] = frozenset()
    paths: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Role:
    name: str
    tier: int
    allow: Access
    deny: Access = field(default_factory=Access)
    preferred_models: tuple[str, ...] = ()
    public: bool = False

    def enforce(self, kind: str, value: str) -> None:
        denied = getattr(self.deny, kind)
        folded = value.casefold()
        if any(item.casefold() in folded for item in denied):
            raise Denied(f"{self.name} denies {kind[:-1]} {value!r}")
        allowed = getattr(self.allow, kind)
        if allowed and value not in allowed:
            if kind != "paths" or not any(_within(value, root) for root in allowed):
                raise Denied(f"{self.name} does not allow {kind[:-1]} {value!r}")


def _within(value: str, root: str) -> bool:
    try:
        path, base = Path(value).resolve(), Path(root).resolve()
        return path == base or base in path.parents
    except OSError:
        return False


def registry(workspace: Path) -> dict[str, Role]:
    gemini = ("gemini-3.6-flash", "gemini-3.5-flash-lite")
    nim = ("nvidia/nemotron-3-nano-30b-a3b",)
    return {
        "voice": Role("voice", 0, Access(tools=frozenset({"handoff"}),
                      channels=frozenset({"dm-asa", "project-brokie"})),
                      preferred_models=gemini + nim),
        "builder": Role("builder", 2,
            Access(tools=frozenset({"write_file"}), channels=frozenset({"project-brokie"}),
                   paths=frozenset({str(workspace)})),
            Access(names=frozenset({"Asa"}), hosts=frozenset({"generativelanguage.googleapis.com"})),
            preferred_models=nim + gemini),
        "observer": Role("observer", 2, Access(channels=frozenset({"project-brokie"})),
                         Access(names=frozenset({"Asa"})), preferred_models=gemini + nim),
    }
