"""A role is mostly a crisp list of doors that stay shut."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class Denied(PermissionError):
    pass


class BriefLeak(Denied):
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
    harness: str = "pi"
    os_user: str = "leaf"
    public: bool = False

    def enforce(self, kind: str, value: str) -> None:
        denied = getattr(self.deny, kind)
        folded = value.casefold()
        for item in denied:
            if item.casefold() in folded:
                raise Denied(f"{self.name} denies {kind[:-1]} {value!r}")
        allowed = getattr(self.allow, kind)
        if allowed:
            if kind == "paths":
                if not any(_within(value, root) for root in allowed):
                    raise Denied(f"{self.name} does not allow path {value!r}")
            elif value not in allowed:
                raise Denied(f"{self.name} does not allow {kind[:-1]} {value!r}")


def _within(value: str, root: str) -> bool:
    try:
        p, b = Path(value).resolve(), Path(root).resolve()
        return p == b or b in p.parents
    except OSError:
        return False


def default_registry(workspace: Path) -> dict[str, Role]:
    gemini = ("gemini-3.5-flash-lite", "gemini-3.6-flash")
    nim = ("nvidia/nemotron-3-nano-30b-a3b",)
    return {
        "voice": Role("voice", 0, Access(tools=frozenset({"handoff"}),
                      channels=frozenset({"dm-asa", "project-brokie"})), preferred_models=gemini + nim),
        "builder": Role("builder", 2, Access(tools=frozenset({"write_file"}), channels=frozenset({"project-brokie"}),
                        paths=frozenset({str(workspace)})), Access(names=frozenset({"Asa"}),
                        hosts=frozenset({"generativelanguage.googleapis.com"})), preferred_models=gemini + nim),
        "observer": Role("observer", 2, Access(channels=frozenset({"project-brokie"})),
                         Access(names=frozenset({"Asa"})), preferred_models=nim + gemini),
    }
