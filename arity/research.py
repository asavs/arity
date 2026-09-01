"""Bounded, read-only retrieval surfaces for research personalities.

The candidate workspace and the project being researched are different trust
boundaries.  ``ResearchScope`` exposes only targeted reads from the latter and
sanitized, completed history from Arity's query-only record readers.  It never
hands an agent a filesystem path to the record store or a writable store.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from .record_readers import (
    RecordReadLimits,
    RecordReadError,
    StoreSpec,
    configured_store_spec,
    open_record_reader,
)


MAX_PROJECT_READ_LINES = 200
MAX_PROJECT_FILE_BYTES = 512 * 1024
MAX_PROJECT_RESULTS = 50
MAX_PROJECT_SCAN_FILES = 5_000
MAX_PROJECT_SCAN_BYTES = 8 * 1024 * 1024
MAX_RECORD_RESULTS = 50
MAX_RECORD_VALUE_CHARS = 1_000
MAX_RECORD_COLLECTION_ITEMS = 100
MAX_RECORD_OUTPUT_CHARS = 64 * 1024

DEFAULT_RESEARCH_RECORD_LIMITS = RecordReadLimits(
    max_snapshot_bytes=8 * 1024 * 1024,
    max_query_records=1_024,
)

# Deliberately excludes raw turns, tool results, evidence bundles, journal
# events, messages, and usage telemetry.  ``terrarium_trial`` is useful for
# runtime research, but only after its task has a completed resolution and only
# after its output/test payloads have been removed.
RESEARCH_RECORD_KINDS = frozenset(
    {
        "archivist_entry",
        "evaluation",
        "human_pick",
        "judgement",
        "resolution",
        "scorecard",
        "terrarium_trial",
        "trial_axes",
    }
)

_BLOCKED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".claude",
        ".codex",
        ".terrarium",
        ".omp",
        ".hidden_tests",
        "hidden_tests",
        ".venv",
        "node_modules",
        "__pycache__",
    }
)
_BLOCKED_FILE_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "records.db",
        "secrets.json",
        "auth.json",
    }
)
_BLOCKED_FILE_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
_RECORD_REFERENCE_FIELDS = {
    "task_id": "task_ref",
    "trial_id": "trial_ref",
    "candidate_id": "candidate_ref",
    "evidence_hash": "evidence_ref",
    "evaluation_id": "evaluation_ref",
    "resolution_id": "resolution_ref",
}
_RECORD_PROJECTION_FIELDS: dict[str, frozenset[str]] = {
    "archivist_entry": frozenset(
        {"task_id", "candidate_id", "role", "verdict", "discrepancy"}
    ),
    "evaluation": frozenset(
        {"task_id", "evaluation_id", "evidence_hash", "schema_version"}
    ),
    "human_pick": frozenset({"task_id", "evidence_hash", "picked"}),
    "judgement": frozenset(
        {
            "task_id",
            "evidence_hash",
            "status",
            "parsed",
            "ranked_own_model_first",
            "tokens_used",
            "duration_seconds",
        }
    ),
    "resolution": frozenset(
        {
            "task_id",
            "resolution_id",
            "evidence_hash",
            "schema_version",
            "status",
            "source",
        }
    ),
    "scorecard": frozenset(
        {"task_id", "role", "verdict", "score_delta", "standing_after"}
    ),
    "terrarium_trial": frozenset(
        {
            "task_id",
            "candidate_id",
            "role",
            "status",
            "fallbacks",
            "tokens_used",
            "duration_seconds",
        }
    ),
    "trial_axes": frozenset(
        {
            "task_id",
            "candidate_id",
            "tier",
            "false_claims",
            "confessed",
            "hidden_rate",
            "own_rate",
            "hidden_total",
            "own_total",
            "tokens",
            "seconds",
            "fallbacks",
            "changed_files",
            "phase_tokens",
            "model_turns",
            "tool_calls",
            "tool_errors",
            "friction",
            "prompt_tokens",
            "completion_tokens",
            "prompt_per_turn",
            "test_runs",
            "fetch_calls",
            "fetch_errors",
            "py_files",
            "loc",
            "test_files",
            "test_count",
            "type_ignores",
            "bare_asserts",
            "compile_ok",
            "brief_numbers",
            "brief_numbers_in_own_tests",
        }
    ),
}
_CONTROLLED_RECORD_STRINGS = frozenset({"role", "status", "source", "verdict"})
_CONTROLLED_RECORD_STRING = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
_KIND_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\Z")
_FILTER_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")


class ResearchAccessError(ValueError):
    """A requested research read falls outside its bounded scope."""


def _positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ResearchAccessError(f"{label} must be a positive integer")
    return value


def _is_sensitive_relative_path(path: Path, denied_paths: Iterable[str] = ()) -> bool:
    normalized = path.as_posix().lower()
    parts = tuple(part.lower() for part in path.parts)
    if any(part in _BLOCKED_DIRECTORY_NAMES for part in parts):
        return True
    name = path.name.lower()
    if (
        name in _BLOCKED_FILE_NAMES
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith(_BLOCKED_FILE_SUFFIXES)
    ):
        return True
    # Only declarative public extensions are visible under .arity. Runtime
    # config, auth, and persistence remain behind dedicated host boundaries.
    if parts and parts[0] == ".arity":
        if len(parts) >= 2 and parts[1] not in {"roles", "skills"}:
            return True
    return any(str(denied).replace("\\", "/").lower() in normalized for denied in denied_paths if denied)


def _neutral_reference(field_name: str, value: str) -> str:
    digest = hashlib.sha256(f"{field_name}:{value}".encode("utf-8")).hexdigest()[:12]
    return f"ref-{digest}"


def _project_record(kind: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """Positive projection: persisted fields are private unless named here."""
    allowed = _RECORD_PROJECTION_FIELDS[kind]
    projected: dict[str, Any] = {}
    for field_name in allowed:
        if field_name not in record:
            continue
        value = record[field_name]
        output_name = _RECORD_REFERENCE_FIELDS.get(field_name, field_name)
        if field_name in _RECORD_REFERENCE_FIELDS:
            if isinstance(value, str) and value:
                projected[output_name] = _neutral_reference(field_name, value)
            continue
        if field_name == "picked":
            projected["selection_recorded"] = isinstance(value, str) and bool(value)
            continue
        if field_name in _CONTROLLED_RECORD_STRINGS:
            if isinstance(value, str) and _CONTROLLED_RECORD_STRING.fullmatch(value):
                projected[field_name] = value
            continue
        # Booleans are intentionally checked before numbers because bool is an
        # int subclass in Python.  Free-form strings, containers, and unknown
        # objects never cross this boundary.
        if type(value) is bool:
            projected[field_name] = value
        elif type(value) in {int, float}:
            projected[field_name] = value
    return projected


def _resolved_task_ids(reader: Any) -> set[str]:
    try:
        resolutions = reader.query("resolution")
    except RecordReadError:
        return set()
    completed: set[str] = set()
    for record in resolutions:
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status", "")).lower()
        source = str(record.get("source", "")).lower()
        if status not in {"resolved", "completed", "complete"} and (
            source in {"", "unresolved"} or record.get("candidate_id") is None
        ):
            continue
        task_id = record.get("task_id")
        if isinstance(task_id, str) and task_id:
            completed.add(task_id)
    return completed


@dataclass(frozen=True)
class ResearchScope:
    """Configured roots and budgets for one read-only research capability.

    ``record_spec`` names a built-in store for strict query-only snapshots.  A
    relative store path is resolved under ``project_root`` once, before Scout is
    dispatched.  No store path is included in the tool schema or response.
    """

    project_root: Path
    work_root: Path | None = None
    record_spec: StoreSpec | None = None
    record_limits: RecordReadLimits = field(
        default_factory=lambda: DEFAULT_RESEARCH_RECORD_LIMITS
    )

    def __post_init__(self) -> None:
        root = Path(self.project_root).resolve()
        if not root.is_dir():
            raise ResearchAccessError(f"research project root is not a directory: {root}")
        object.__setattr__(self, "project_root", root)
        if self.work_root is not None:
            work_root = Path(self.work_root).resolve()
            if not work_root.is_dir():
                raise ResearchAccessError(
                    f"research work root is not a directory: {work_root}"
                )
            object.__setattr__(self, "work_root", work_root)
        spec = self.record_spec or configured_store_spec()
        path = spec.path if spec.path.is_absolute() else root / spec.path
        object.__setattr__(self, "record_spec", StoreSpec(spec.backend, path.resolve()))
        if type(self.record_limits) is not RecordReadLimits:
            raise TypeError("record_limits must be an exact RecordReadLimits")

    @classmethod
    def configured(cls, project_root: Path | str | None = None) -> "ResearchScope":
        """Build the default scope from the active project and Arity store config."""
        return cls(project_root=Path(project_root) if project_root is not None else Path.cwd())

    def with_work_root(self, work_root: Path | str) -> "ResearchScope":
        """Bind one requester's exact workspace without widening the project root."""
        return replace(self, work_root=Path(work_root))

    def _selected_root(self, scope: str) -> Path:
        if scope == "project":
            return self.project_root
        if scope == "work" and self.work_root is not None:
            return self.work_root
        if scope == "work":
            raise ResearchAccessError("no requesting work root is configured")
        raise ResearchAccessError("scope must be 'project' or 'work'")

    def _resolve(
        self,
        relative_path: str,
        denied_paths: Iterable[str] = (),
        *,
        scope: str = "project",
    ) -> tuple[Path, Path]:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ResearchAccessError("project path must be a nonblank relative path")
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ResearchAccessError("project paths must be relative to the configured research root")
        selected_root = self._selected_root(scope)
        target = (selected_root / candidate).resolve()
        try:
            relative = target.relative_to(selected_root)
        except ValueError as exc:
            raise ResearchAccessError("project path escapes the configured research root") from exc
        if _is_sensitive_relative_path(relative, denied_paths):
            raise ResearchAccessError(f"project path is outside the research surface: {relative.as_posix()}")
        return target, relative

    def read_project_file(
        self,
        path: str,
        offset: int = 1,
        limit: int = 100,
        scope: str = "project",
        *,
        denied_paths: Iterable[str] = (),
    ) -> str:
        """Read one capped line range from a non-sensitive project file."""
        target, relative = self._resolve(path, denied_paths, scope=scope)
        if not target.is_file():
            raise ResearchAccessError(f"project file does not exist: {relative.as_posix()}")
        size = target.stat().st_size
        if size > MAX_PROJECT_FILE_BYTES:
            raise ResearchAccessError(
                f"project file exceeds the {MAX_PROJECT_FILE_BYTES}-byte research limit"
            )
        start = _positive_int(offset, label="offset")
        requested = _positive_int(limit, label="limit")
        capped = min(requested, MAX_PROJECT_READ_LINES)
        raw = target.read_bytes()
        if b"\0" in raw:
            raise ResearchAccessError("binary project files are outside the text research surface")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        selected = lines[start - 1 : start - 1 + capped]
        end = start + len(selected) - 1
        heading = f"[{relative.as_posix()} lines {start}-{max(start, end)}]"
        body = "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(selected, start=start)
        )
        suffix = "\n[limit capped]" if requested > capped else ""
        return f"{heading}\n{body or '(no lines in range)'}{suffix}"

    def _visible_files(
        self,
        path: str,
        glob: str,
        denied_paths: Iterable[str],
        scope: str,
    ) -> Iterable[tuple[Path, Path]]:
        selected_root = self._selected_root(scope)
        target, _ = self._resolve(path, denied_paths, scope=scope)
        if not target.exists():
            raise ResearchAccessError(f"project path does not exist: {path}")
        if target.is_file():
            candidates: Iterable[Path] = (target,)
        else:
            def walked_files() -> Iterable[Path]:
                for directory, dir_names, file_names in os.walk(target, followlinks=False):
                    directory_path = Path(directory)
                    kept_dirs: list[str] = []
                    for name in dir_names:
                        try:
                            relative_dir = (directory_path / name).resolve().relative_to(selected_root)
                        except (OSError, ValueError):
                            continue
                        if not _is_sensitive_relative_path(relative_dir, denied_paths):
                            kept_dirs.append(name)
                    dir_names[:] = kept_dirs
                    for name in file_names:
                        yield directory_path / name

            candidates = walked_files()
        seen = 0
        for candidate in candidates:
            if seen >= MAX_PROJECT_SCAN_FILES:
                break
            seen += 1
            try:
                resolved = candidate.resolve()
                relative = resolved.relative_to(selected_root)
            except (OSError, ValueError):
                continue
            if not resolved.is_file() or _is_sensitive_relative_path(relative, denied_paths):
                continue
            if glob and not fnmatch.fnmatch(relative.as_posix(), glob):
                continue
            yield resolved, relative

    def list_project(
        self,
        path: str = ".",
        glob: str = "*",
        limit: int = 50,
        scope: str = "project",
        *,
        denied_paths: Iterable[str] = (),
    ) -> str:
        """Find project files by a bounded glob without exposing hidden paths."""
        requested = _positive_int(limit, label="limit")
        capped = min(requested, MAX_PROJECT_RESULTS)
        found: list[str] = []
        for _, relative in self._visible_files(path, glob, denied_paths, scope):
            found.append(relative.as_posix())
            if len(found) >= capped:
                break
        suffix = "\n[limit capped]" if requested > capped else ""
        return ("\n".join(found) if found else "(no project files matched)") + suffix

    def search_project(
        self,
        pattern: str,
        path: str = ".",
        glob: str = "*",
        limit: int = 50,
        scope: str = "project",
        *,
        denied_paths: Iterable[str] = (),
    ) -> str:
        """Search bounded project text and return exact file/line matches."""
        if not isinstance(pattern, str) or not pattern:
            raise ResearchAccessError("search pattern must be a nonblank regex")
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ResearchAccessError(f"invalid search regex: {exc}") from exc
        requested = _positive_int(limit, label="limit")
        capped = min(requested, MAX_PROJECT_RESULTS)
        matches: list[str] = []
        scanned_bytes = 0
        for candidate, relative in self._visible_files(path, glob, denied_paths, scope):
            try:
                size = candidate.stat().st_size
                if size > MAX_PROJECT_FILE_BYTES:
                    continue
                scanned_bytes += size
                if scanned_bytes > MAX_PROJECT_SCAN_BYTES:
                    break
                raw = candidate.read_bytes()
                if b"\0" in raw:
                    continue
                lines = raw.decode("utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if regex.search(line):
                    excerpt = line.strip()
                    if len(excerpt) > 300:
                        excerpt = excerpt[:300] + "..."
                    matches.append(f"{relative.as_posix()}:{line_number}: {excerpt}")
                    if len(matches) >= capped:
                        break
            if len(matches) >= capped:
                break
        suffix = "\n[limit capped]" if requested > capped else ""
        return ("\n".join(matches) if matches else "(no project matches)") + suffix

    def query_records(
        self,
        kind: str,
        filters: Mapping[str, Any] | None = None,
        limit: int = 20,
        *,
        excluded_ids: Iterable[str] = (),
    ) -> str:
        """Return a capped, sanitized projection of completed historical records."""
        if not isinstance(kind, str) or _KIND_PATTERN.fullmatch(kind) is None:
            raise ResearchAccessError("record kind must be a simple name")
        if kind not in RESEARCH_RECORD_KINDS:
            allowed = ", ".join(sorted(RESEARCH_RECORD_KINDS))
            raise ResearchAccessError(
                f"record kind '{kind}' is outside the research projection; allowed: {allowed}"
            )
        selected_filters = dict(filters or {})
        projected_filter_fields = {
            ("selection_recorded" if field == "picked" else field)
            for field in _RECORD_PROJECTION_FIELDS[kind]
            if field not in _RECORD_REFERENCE_FIELDS
        }
        if len(selected_filters) > 8 or any(
            not isinstance(key, str) or _FILTER_KEY_PATTERN.fullmatch(key) is None
            for key in selected_filters
        ):
            raise ResearchAccessError("record filters must contain at most 8 simple top-level keys")
        if any(key not in projected_filter_fields for key in selected_filters):
            raise ResearchAccessError("record filters may use only public projected fields")
        for key, value in selected_filters.items():
            if key in _CONTROLLED_RECORD_STRINGS:
                if not isinstance(value, str) or _CONTROLLED_RECORD_STRING.fullmatch(value) is None:
                    raise ResearchAccessError("record string filters must be controlled identifiers")
            elif type(value) not in {bool, int, float}:
                raise ResearchAccessError("record metric filters must be booleans or numbers")
        requested = _positive_int(limit, label="limit")
        capped = min(requested, MAX_RECORD_RESULTS)
        excluded = {value for value in excluded_ids if isinstance(value, str) and value}
        assert self.record_spec is not None
        try:
            with open_record_reader(self.record_spec, limits=self.record_limits) as reader:
                completed_ids = _resolved_task_ids(reader)
                records = reader.query(kind)
        except (OSError, RecordReadError) as exc:
            raise ResearchAccessError("record history is unavailable") from exc

        projected: list[dict[str, Any]] = []
        output_capped = False
        for record in records:
            if not isinstance(record, Mapping):
                continue
            identifiers = {
                value
                for key in ("task_id", "trial_id", "candidate_id", "evidence_hash")
                if isinstance((value := record.get(key)), str)
            }
            if excluded & identifiers:
                continue
            status = str(record.get("status", "")).lower()
            if status in {"active", "in_progress", "in-progress", "pending", "running", "started", "unresolved"}:
                continue
            task_id = record.get("task_id")
            if isinstance(task_id, str) and task_id not in completed_ids:
                continue
            safe_record = _project_record(kind, record)
            if any(
                type(safe_record.get(key)) is not type(value) or safe_record.get(key) != value
                for key, value in selected_filters.items()
            ):
                continue
            projected.append(safe_record)
            if len(json.dumps(projected, ensure_ascii=False)) > MAX_RECORD_OUTPUT_CHARS:
                projected.pop()
                output_capped = True
                break
            if len(projected) >= capped:
                break
        response = {
            "kind": kind,
            "count": len(projected),
            "records": projected,
            "truncated": output_capped or len(projected) >= capped or requested > capped,
        }
        return json.dumps(response, ensure_ascii=False, sort_keys=True)


__all__ = [
    "DEFAULT_RESEARCH_RECORD_LIMITS",
    "MAX_PROJECT_READ_LINES",
    "MAX_PROJECT_RESULTS",
    "MAX_RECORD_RESULTS",
    "RESEARCH_RECORD_KINDS",
    "ResearchAccessError",
    "ResearchScope",
]
