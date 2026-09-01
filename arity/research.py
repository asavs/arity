"""Bounded, read-only retrieval surfaces for research personalities.

The candidate workspace and the project being researched are different trust
boundaries.  ``ResearchScope`` exposes only targeted reads from the latter and
sanitized, completed history from Arity's query-only record readers.  It never
hands an agent a filesystem path to the record store or a writable store.
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence import EvidenceBundle, Evaluation, Resolution
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
MAX_PROJECT_READ_OUTPUT_BYTES = 65_536
MAX_PROJECT_SEARCH_PATTERN_CHARS = 256
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
        "evidence_bundle",
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
        ".hidden-tests",
        "hidden_tests",
        "hidden-tests",
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
    "evidence_bundle": frozenset({"task_id", "task_name", "schema_version"}),
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
_CANONICAL_TASK_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
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
    # Project-local Arity definitions are executable configuration, not
    # research data; all .arity state stays behind dedicated host boundaries.
    if parts and parts[0] == ".arity":
        return True
    return any(str(denied).replace("\\", "/").lower() in normalized for denied in denied_paths if denied)


def _neutral_reference(reference_key: bytes, field_name: str, value: str) -> str:
    digest = hmac.new(
        reference_key,
        b"arity.research.ref.v1\0"
        + field_name.encode("utf-8")
        + b"\0"
        + value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]
    return f"ref-{digest}"


def _project_record(
    reference_key: bytes, kind: str, record: Mapping[str, Any]
) -> dict[str, Any]:
    """Positive projection: persisted fields are private unless named here."""
    allowed = _RECORD_PROJECTION_FIELDS[kind]
    projected: dict[str, Any] = {}
    if kind == "evidence_bundle":
        task_name = record.get("task_name")
        if isinstance(task_name, str) and _CANONICAL_TASK_NAME.fullmatch(task_name):
            projected["task_name"] = task_name
        candidates = record.get("candidates")
        if isinstance(candidates, (list, tuple)):
            projected["candidate_count"] = len(candidates)
        metadata = record.get("metadata")
        if isinstance(metadata, Mapping):
            requested_arity = metadata.get("requested_arity")
            if type(requested_arity) is int and requested_arity >= 0:
                projected["requested_arity"] = requested_arity
    for field_name in allowed:
        if field_name not in record:
            continue
        value = record[field_name]
        output_name = _RECORD_REFERENCE_FIELDS.get(field_name, field_name)
        if field_name in _RECORD_REFERENCE_FIELDS:
            if isinstance(value, str) and value:
                projected[output_name] = _neutral_reference(
                    reference_key, field_name, value
                )
            continue
        if field_name == "picked":
            projected["selection_recorded"] = isinstance(value, str) and bool(value)
            continue
        if field_name == "task_name":
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


@dataclass(frozen=True)
class _ValidatedResearchHistory:
    """Content-addressed records that participate in a valid final resolution."""

    bundles: Mapping[tuple[str, str], EvidenceBundle]
    evaluations: Mapping[tuple[str, str], Evaluation]
    resolved_bundle_keys: frozenset[tuple[str, str]]
    resolution_keys: frozenset[tuple[str, str]]

    @property
    def resolved_task_ids(self) -> frozenset[str]:
        return frozenset(task_id for task_id, _ in self.resolved_bundle_keys)

    def candidate_ids(self, task_id: str) -> frozenset[str]:
        return frozenset(
            candidate.candidate_id
            for key, bundle in self.bundles.items()
            if key in self.resolved_bundle_keys and key[0] == task_id
            for candidate in bundle.candidates
        )


def _validated_history(reader: Any) -> _ValidatedResearchHistory:
    """Validate persisted evidence, evaluations, and final resolutions."""
    bundle_rows = reader.query("evidence_bundle")
    evaluation_rows = reader.query("evaluation")
    resolutions = reader.query("resolution")

    bundles: dict[tuple[str, str], EvidenceBundle] = {}
    for record in bundle_rows:
        if not isinstance(record, Mapping):
            continue
        try:
            bundle = EvidenceBundle.from_dict(record)
        except (KeyError, TypeError, ValueError):
            continue
        if bundle.task_id:
            bundles[(bundle.task_id, bundle.evidence_hash)] = bundle

    evaluations: dict[tuple[str, str], Evaluation] = {}
    for record in evaluation_rows:
        if not isinstance(record, Mapping):
            continue
        task_id = record.get("task_id")
        evidence_hash = record.get("evidence_hash")
        if not isinstance(task_id, str) or not isinstance(evidence_hash, str):
            continue
        bundle = bundles.get((task_id, evidence_hash))
        if bundle is None:
            continue
        try:
            evaluation = Evaluation.from_dict(bundle, record)
        except (KeyError, TypeError, ValueError):
            continue
        evaluations[(task_id, evaluation.evaluation_id)] = evaluation

    resolved_bundle_keys: set[tuple[str, str]] = set()
    resolution_keys: set[tuple[str, str]] = set()
    for record in resolutions:
        if not isinstance(record, Mapping):
            continue
        task_id = record.get("task_id")
        evidence_hash = record.get("evidence_hash")
        if not isinstance(task_id, str) or not isinstance(evidence_hash, str):
            continue
        bundle = bundles.get((task_id, evidence_hash))
        if bundle is None:
            continue
        try:
            resolution = Resolution.from_dict(record)
            referenced = tuple(
                evaluations[(task_id, evaluation_id)]
                for evaluation_id in resolution.evaluation_ids
            )
            resolution.validate(bundle, referenced)
        except (KeyError, TypeError, ValueError):
            continue
        if resolution.resolved:
            resolved_bundle_keys.add((task_id, evidence_hash))
            resolution_keys.add((task_id, resolution.resolution_id))
    return _ValidatedResearchHistory(
        bundles=bundles,
        evaluations=evaluations,
        resolved_bundle_keys=frozenset(resolved_bundle_keys),
        resolution_keys=frozenset(resolution_keys),
    )


def _belongs_to_validated_history(
    history: _ValidatedResearchHistory,
    kind: str,
    record: Mapping[str, Any],
) -> bool:
    """Require projected lifecycle records to match the validated object exactly."""
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or task_id not in history.resolved_task_ids:
        return False

    if kind == "evidence_bundle":
        try:
            bundle = EvidenceBundle.from_dict(record)
        except (KeyError, TypeError, ValueError):
            return False
        return (task_id, bundle.evidence_hash) in history.resolved_bundle_keys

    if kind == "evaluation":
        evidence_hash = record.get("evidence_hash")
        if not isinstance(evidence_hash, str):
            return False
        bundle = history.bundles.get((task_id, evidence_hash))
        if bundle is None or (task_id, evidence_hash) not in history.resolved_bundle_keys:
            return False
        try:
            evaluation = Evaluation.from_dict(bundle, record)
        except (KeyError, TypeError, ValueError):
            return False
        return history.evaluations.get((task_id, evaluation.evaluation_id)) == evaluation

    if kind == "resolution":
        try:
            resolution = Resolution.from_dict(record)
        except (KeyError, TypeError, ValueError):
            return False
        return (
            (task_id, resolution.evidence_hash) in history.resolved_bundle_keys
            and (task_id, resolution.resolution_id) in history.resolution_keys
        )

    evidence_hash = record.get("evidence_hash")
    if kind in {"human_pick", "judgement"} and (
        not isinstance(evidence_hash, str)
        or (task_id, evidence_hash) not in history.resolved_bundle_keys
    ):
        return False

    candidate_id = record.get("candidate_id")
    if kind in {"archivist_entry", "terrarium_trial", "trial_axes"} and (
        not isinstance(candidate_id, str)
        or candidate_id not in history.candidate_ids(task_id)
    ):
        return False
    if kind == "human_pick":
        picked = record.get("picked")
        if picked is not None and (
            not isinstance(picked, str)
            or picked not in history.candidate_ids(task_id)
        ):
            return False
    return True


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
    personalities: frozenset[str] = field(
        default=frozenset({"scout"}), kw_only=True
    )
    _reference_key: bytes = field(
        default_factory=lambda: secrets.token_bytes(32),
        repr=False,
        compare=False,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        root = Path(self.project_root).resolve()
        if not root.is_dir():
            raise ResearchAccessError(f"research project root is not a directory: {root}")
        broad_roots = {Path(root.anchor).resolve(), Path.home().resolve()}
        if root in broad_roots:
            raise ResearchAccessError(
                "research project root must be a specific project, not a filesystem or home root"
            )
        object.__setattr__(self, "project_root", root)
        if type(self._reference_key) is not bytes or len(self._reference_key) != 32:
            raise ResearchAccessError("research reference key must contain 32 random bytes")
        if not self.personalities or any(
            not isinstance(name, str) or not name.strip() for name in self.personalities
        ):
            raise ResearchAccessError("research personalities must be nonblank names")
        object.__setattr__(
            self,
            "personalities",
            frozenset(name.lower().strip() for name in self.personalities),
        )
        if self.work_root is not None:
            work_root = Path(self.work_root).resolve()
            if not work_root.is_dir():
                raise ResearchAccessError(
                    f"research work root is not a directory: {work_root}"
                )
            work_broad_roots = {
                Path(work_root.anchor).resolve(),
                Path.home().resolve(),
            }
            if work_root in work_broad_roots:
                raise ResearchAccessError(
                    "research work root must be a specific workspace, not a filesystem or home root"
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
        if project_root is not None:
            return cls(project_root=Path(project_root))
        start = Path.cwd().resolve()
        broad_roots = {Path(start.anchor).resolve(), Path.home().resolve()}
        discovered = next(
            (
                candidate
                for candidate in (start, *start.parents)
                if candidate not in broad_roots
                and (
                    (candidate / "pyproject.toml").is_file()
                    or (candidate / ".git").exists()
                )
            ),
            None,
        )
        return cls(project_root=discovered or start)

    def with_work_root(self, work_root: Path | str) -> "ResearchScope":
        """Bind one requester's exact workspace without widening the project root."""
        return replace(self, work_root=Path(work_root))

    def grants(self, personality_name: str) -> bool:
        """Whether this host-issued scope names a personality as its recipient."""
        return personality_name.lower().strip() in self.personalities

    def _selected_root(self, scope: str) -> Path:
        if scope == "project":
            return self.project_root
        if scope == "work" and self.work_root is not None:
            return self.work_root
        if scope == "work":
            raise ResearchAccessError("no requesting work root is configured")
        raise ResearchAccessError("scope must be 'project' or 'work'")

    def _is_record_storage_path(self, target: Path) -> bool:
        """Hide the configured store and SQLite companions from raw project reads."""
        assert self.record_spec is not None
        store_path = self.record_spec.path.resolve()
        if self.record_spec.backend == "jsonl":
            try:
                target.resolve().relative_to(store_path)
                return True
            except ValueError:
                return False
        resolved = target.resolve()
        return resolved == store_path or (
            resolved.parent == store_path.parent
            and resolved.name.startswith(store_path.name + "-")
        )

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
        if self._is_record_storage_path(target) or _is_sensitive_relative_path(relative, denied_paths):
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
        rendered = f"{heading}\n{body or '(no lines in range)'}{suffix}"
        encoded = rendered.encode("utf-8")
        if len(encoded) <= MAX_PROJECT_READ_OUTPUT_BYTES:
            return rendered
        marker = "\n[output capped]"
        budget = MAX_PROJECT_READ_OUTPUT_BYTES - len(marker.encode("utf-8"))
        return encoded[:budget].decode("utf-8", errors="ignore") + marker

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
                        resolved_dir = (directory_path / name).resolve()
                        if (
                            not self._is_record_storage_path(resolved_dir)
                            and not _is_sensitive_relative_path(relative_dir, denied_paths)
                        ):
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
            if (
                not resolved.is_file()
                or self._is_record_storage_path(resolved)
                or _is_sensitive_relative_path(relative, denied_paths)
            ):
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
        """Search bounded project text for literal alternatives."""
        if not isinstance(pattern, str) or not pattern:
            raise ResearchAccessError("search pattern must be nonblank text")
        if len(pattern) > MAX_PROJECT_SEARCH_PATTERN_CHARS:
            raise ResearchAccessError(
                f"search pattern exceeds {MAX_PROJECT_SEARCH_PATTERN_CHARS} characters"
            )
        alternatives = tuple(
            part.casefold() for part in pattern.split("|") if part
        )
        if not alternatives:
            raise ResearchAccessError("search pattern must include literal text")
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
                folded = line.casefold()
                if any(alternative in folded for alternative in alternatives):
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
            (
                _RECORD_REFERENCE_FIELDS[field]
                if field in _RECORD_REFERENCE_FIELDS
                else "selection_recorded" if field == "picked" else field
            )
            for field in _RECORD_PROJECTION_FIELDS[kind]
        }
        if kind == "evidence_bundle":
            projected_filter_fields.update(
                {"candidate_count", "requested_arity"}
            )
        if len(selected_filters) > 8 or any(
            not isinstance(key, str) or _FILTER_KEY_PATTERN.fullmatch(key) is None
            for key in selected_filters
        ):
            raise ResearchAccessError("record filters must contain at most 8 simple top-level keys")
        if any(key not in projected_filter_fields for key in selected_filters):
            raise ResearchAccessError("record filters may use only public projected fields")
        for key, value in selected_filters.items():
            if key in _RECORD_REFERENCE_FIELDS.values():
                if not isinstance(value, str) or re.fullmatch(r"ref-[0-9a-f]{12}", value) is None:
                    raise ResearchAccessError("record reference filters must use neutral projected refs")
            elif key == "task_name":
                if not isinstance(value, str) or _CANONICAL_TASK_NAME.fullmatch(value) is None:
                    raise ResearchAccessError("task_name filters require a canonical task name")
            elif key in _CONTROLLED_RECORD_STRINGS:
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
                history = _validated_history(reader)
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
            if not _belongs_to_validated_history(history, kind, record):
                continue
            safe_record = _project_record(self._reference_key, kind, record)
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
