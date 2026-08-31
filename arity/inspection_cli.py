"""ANSI-free terminal and machine renderers for read-only trial inspection."""
from __future__ import annotations

import json
import math
import sys
import unicodedata
from argparse import Namespace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from .inspection import (
    TRIAL_INSPECTION_API_VERSION,
    InspectionIssue,
    TrialCatalog,
    TrialInspection,
    TrialNotFound,
    inspect_trial,
    inspect_trials,
)
from .record_readers import (
    RecordChanged,
    RecordCorruption,
    RecordNotFound,
    RecordReadError,
    open_record_reader,
)


EXIT_OK = 0
EXIT_OPERATIONAL = 1
EXIT_NOT_FOUND = 3
EXIT_UNSUPPORTED = 4
EXIT_CORRUPT = 5

_SAFE_EVENT_TYPES = {
    "trial.started",
    "arm.completed",
    "evidence.frozen",
    "review.recorded",
    "resolution.recorded",
    "delivery.completed",
}
_SAFE_ISSUE_MESSAGES = {
    "invalid_record": "A persisted trial record is not valid event data.",
    "orphan_event": "A persisted event has no usable trial identity.",
    "invalid_event": "A persisted trial event envelope is invalid.",
    "invalid_replay": "The trial journal violates lifecycle invariants.",
    "unsupported_event": "The trial contains an event type this version does not understand.",
    "unsupported_event_schema": "The trial contains a newer event schema.",
    "unsupported_evidence_schema": "The trial contains a newer evidence schema.",
    "unsupported_evaluation_schema": "The trial contains a newer evaluation schema.",
    "unsupported_resolution_schema": "The trial contains a newer resolution schema.",
}


def _terminal_safe(value: Any) -> str:
    """Render persisted text visibly without executing terminal control syntax."""
    escaped: list[str] = []
    for character in str(value):
        codepoint = ord(character)
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _human_print(*values: Any, file: Any = None) -> None:
    stream = file or sys.stdout
    if values:
        stream.write(" ".join(_terminal_safe(value) for value in values))
    stream.write("\n")
    stream.flush()


def _safe_issue_dict(issue: InspectionIssue) -> dict[str, Any]:
    """Project diagnostics without echoing nested persisted values."""
    encoded = issue.to_dict()
    encoded["message"] = _SAFE_ISSUE_MESSAGES.get(
        issue.code, "The persisted trial could not be fully inspected."
    )
    if encoded["event_type"] not in _SAFE_EVENT_TYPES:
        encoded["event_type"] = None
    return encoded


def _emit_json(value: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()


def _envelope(
    command: str,
    *,
    result: str,
    data: Any,
    error: Optional[Mapping[str, Any]] = None,
    warnings: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "api_version": TRIAL_INSPECTION_API_VERSION,
        "command": command,
        "result": result,
        "data": data,
        "error": None if error is None else dict(error),
        "warnings": [dict(warning) for warning in warnings],
    }


def _format_time(timestamp: Any) -> str:
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return "-"
    try:
        resolved = float(timestamp)
        if not math.isfinite(resolved):
            return str(timestamp)
        return datetime.fromtimestamp(resolved, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, TypeError, ValueError):
        return str(timestamp)


def _one_line(value: Any, *, limit: int = 60) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _integer(value: Any) -> Optional[int]:
    return value if type(value) is int else None


def _number(value: Any) -> Optional[int | float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(float(value)) else None
    except (OverflowError, TypeError, ValueError):
        return None


def _catalog_result(catalog: TrialCatalog) -> tuple[str, int]:
    if catalog.issues or any(trial.integrity == "corrupt" for trial in catalog.trials):
        return "error", EXIT_CORRUPT
    if any(trial.integrity == "unsupported" for trial in catalog.trials):
        return "partial", EXIT_UNSUPPORTED
    return "ok", EXIT_OK


def _inspection_result(inspection: TrialInspection) -> tuple[str, int]:
    if inspection.integrity == "corrupt":
        return "error", EXIT_CORRUPT
    if inspection.integrity == "unsupported":
        return "partial", EXIT_UNSUPPORTED
    return "ok", EXIT_OK


def _result_error(
    result: str,
    *,
    code: str,
    message: str,
    trial_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if result != "error":
        return None
    return {
        "code": code,
        "message": message,
        "trial_id": trial_id,
    }


def _render_table(rows: list[list[str]]) -> None:
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row_number, row in enumerate(rows):
        _human_print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip())
        if row_number == 0:
            _human_print("  ".join("-" * width for width in widths).rstrip())


def render_catalog(catalog: TrialCatalog) -> None:
    summaries = catalog.summaries
    if not summaries:
        _human_print("No persisted trials.")
    else:
        rows = [["TRIAL ID", "STATUS", "INTEGRITY", "ARMS", "EVENTS", "UPDATED (UTC)", "TASK"]]
        for summary in summaries:
            rows.append(
                [
                    _one_line(summary.trial_id, limit=36),
                    summary.status,
                    summary.integrity,
                    f"{summary.completed_arms}/{summary.resolved_arity}",
                    str(summary.event_count),
                    _format_time(summary.updated_at),
                    _one_line(summary.task_name or summary.brief or "ad-hoc", limit=48),
                ]
            )
        _render_table(rows)
        invalid = sum(trial.integrity == "corrupt" for trial in catalog.trials)
        unsupported = sum(trial.integrity == "unsupported" for trial in catalog.trials)
        suffix = []
        if unsupported:
            suffix.append(f"{unsupported} unsupported")
        if invalid:
            suffix.append(f"{invalid} corrupt")
        qualifier = f" ({', '.join(suffix)})" if suffix else ""
        _human_print()
        _human_print(f"{len(summaries)} trial{'s' if len(summaries) != 1 else ''}{qualifier}")
    if catalog.issues:
        _human_print(file=sys.stderr)
        _human_print("Unowned record issues:", file=sys.stderr)
        for issue in catalog.issues:
            _human_print(f"  - {_safe_issue_dict(issue)['message']}", file=sys.stderr)


def _started(inspection: TrialInspection) -> Mapping[str, Any]:
    if inspection.replay is not None:
        return inspection.replay.started
    for event in inspection.events:
        payload = event.get("payload")
        if event.get("event_type") == "trial.started" and isinstance(payload, Mapping):
            return payload
    return {}


def _completion_rows(inspection: TrialInspection) -> list[dict[str, Any]]:
    replay = inspection.replay
    if replay is None:
        return []
    return [dict(completion) for completion in replay.completed_arms]


def _arm_rows(inspection: TrialInspection) -> list[dict[str, Any]]:
    started = _started(inspection)
    declared = started.get("arms") or ()
    if not isinstance(declared, (list, tuple)):
        return []
    completions = _completion_rows(inspection)
    rows: list[dict[str, Any]] = []
    for index, arm in enumerate(declared):
        if isinstance(arm, str):
            arm = {"arm_id": arm, "arm_ordinal": index}
        elif not isinstance(arm, Mapping):
            continue
        arm_id = _string(arm.get("arm_id")) or ""
        arm_completions = [item for item in completions if str(item.get("arm_id", "")) == arm_id]
        latest = arm_completions[-1] if arm_completions else {}
        rows.append(
            {
                "arm_id": arm_id,
                "arm_ordinal": _integer(arm.get("arm_ordinal")) if _integer(arm.get("arm_ordinal")) is not None else index,
                "name": _string(arm.get("name")),
                "model": _string(arm.get("model")),
                "provider": _string(arm.get("provider")),
                "role": _string(arm.get("role")),
                "harness": _string(arm.get("harness")),
                "tool_runner": _string(arm.get("tool_runner")),
                "skills": [item for item in (arm.get("skills") or ()) if isinstance(item, str)]
                if isinstance(arm.get("skills") or (), (list, tuple))
                else [],
                "context": _string(arm.get("context")),
                "context_adapter": _string(arm.get("context_adapter")),
                "phase": (_string(latest.get("phase")) or "trial") if latest else None,
                "candidate_id": _string(latest.get("candidate_id")),
                "completion_status": (
                    _string(latest.get("status")) or "completed"
                    if latest
                    else "pending"
                ),
                "completions": [
                    {
                        "phase": _string(completion.get("phase")) or "trial",
                        "candidate_id": _string(completion.get("candidate_id")),
                        "status": _string(completion.get("status")) or "completed",
                        "tokens_used": _integer(completion.get("tokens_used")),
                        "duration_seconds": _number(completion.get("duration_seconds")),
                        "fallbacks": _integer(completion.get("fallbacks")),
                    }
                    for completion in arm_completions
                ],
            }
        )
    return rows


def inspection_overview(inspection: TrialInspection) -> dict[str, Any]:
    """Return graph-ready metadata without artifact bodies or candidate output text."""
    replay = inspection.replay
    evidence = []
    reviews = []
    resolutions = []
    delivery = None
    if replay is not None:
        for bundle in replay.evidence_bundles:
            evidence.append(
                {
                    "schema_version": bundle.schema_version,
                    "evidence_hash": bundle.evidence_hash,
                    "phase": _string(bundle.metadata.get("phase")) or "trial",
                    "parent_evidence_hash": _string(
                        bundle.metadata.get("parent_evidence_hash")
                    ),
                    "candidates": [
                        {
                            "candidate_id": candidate.candidate_id,
                            "arm_id": candidate.arm_id,
                            "arm_ordinal": candidate.arm_ordinal,
                            "name": candidate.name,
                            "status": candidate.status,
                            "verdict": candidate.verdict,
                            "rank": candidate.rank,
                            "tied_with": list(candidate.tied_with),
                            "artifacts": [
                                {
                                    "path": artifact.path,
                                    "sha256": artifact.sha256,
                                    "size": artifact.size,
                                }
                                for artifact in candidate.artifacts
                            ],
                            "has_output": candidate.output is not None,
                        }
                        for candidate in bundle.candidates
                    ],
                }
            )
        for review in replay.reviews:
            evaluation = review.get("evaluation")
            reviews.append(
                {
                    "evaluator_id": _string(review.get("evaluator_id")),
                    "evidence_hash": _string(review.get("evidence_hash")),
                    "status": _string(review.get("status")),
                    "evaluation_id": (
                        _string(evaluation.get("evaluation_id"))
                        if isinstance(evaluation, Mapping)
                        else None
                    ),
                    "order": (
                        [
                            item
                            for item in (evaluation.get("order") or ())
                            if isinstance(item, str)
                        ]
                        if isinstance(evaluation, Mapping)
                        and isinstance(evaluation.get("order") or (), (list, tuple))
                        else []
                    ),
                }
            )
        resolutions = [
            {
                "schema_version": resolution.schema_version,
                "status": "resolved" if resolution.resolved else "unresolved",
                "resolution_id": resolution.resolution_id,
                "source": resolution.kind.value,
                "candidate_id": resolution.candidate_id,
                "evidence_hash": resolution.evidence_hash,
                "eligible_candidate_ids": list(resolution.eligible_candidate_ids),
                "expected_evaluator_ids": list(resolution.expected_evaluator_ids),
                "evaluator_ids": list(resolution.evaluator_ids),
                "evaluation_ids": list(resolution.evaluation_ids),
            }
            for resolution in replay.resolutions
        ]
        if replay.delivery is not None:
            encoded = replay.delivery.get("delivery")
            encoded = encoded if isinstance(encoded, Mapping) else {}
            delivery = {
                "candidate_id": _string(replay.delivery.get("candidate_id")),
                "resolution_sequence": _integer(
                    replay.delivery.get("resolution_sequence")
                ),
                "resolution_id": _string(replay.delivery.get("resolution_id")),
                "evidence_hash": _string(replay.delivery.get("evidence_hash")),
                "files": [
                    item
                    for item in (encoded.get("files") or ())
                    if isinstance(item, str)
                ]
                if isinstance(encoded.get("files") or (), (list, tuple))
                else [],
                "has_answer": encoded.get("answer") is not None,
                "winner_name": _string(encoded.get("winner_name")),
                "signature": _string(encoded.get("signature")),
                "delivered": (
                    encoded.get("delivered")
                    if isinstance(encoded.get("delivered"), bool)
                    else None
                ),
                "resolution_source": _string(encoded.get("resolution_source")),
            }
    return {
        "api_version": TRIAL_INSPECTION_API_VERSION,
        "trial_id": inspection.trial_id,
        "integrity": inspection.integrity,
        "status": inspection.status,
        "summary": inspection.summary.to_dict(),
        "arms": _arm_rows(inspection),
        "evidence": evidence,
        "reviews": reviews,
        "resolutions": resolutions,
        "delivery": delivery,
        "event_count": len(inspection.events),
        "issues": [_safe_issue_dict(issue) for issue in inspection.issues],
    }


def render_inspection(inspection: TrialInspection) -> None:
    summary = inspection.summary
    _human_print(f"Trial {inspection.trial_id}")
    _human_print(f"  status: {summary.status}")
    _human_print(f"  integrity: {summary.integrity}")
    _human_print(f"  task: {summary.task_name or 'ad-hoc'}")
    _human_print(f"  role: {summary.role or '-'}")
    _human_print(f"  brief: {_one_line(summary.brief, limit=120) or '-'}")
    _human_print(f"  started: {_format_time(summary.started_at)}")
    _human_print(f"  updated: {_format_time(summary.updated_at)}")
    requested = "-" if summary.requested_arity is None else str(summary.requested_arity)
    _human_print(f"  arity: {summary.resolved_arity} resolved / {requested} requested max")

    arms = _arm_rows(inspection)
    _human_print()
    _human_print(f"Arms ({summary.completed_arms}/{summary.resolved_arity})")
    if not arms:
        _human_print("  -")
    for arm in arms:
        adapter = f" adapter={arm['context_adapter']}" if arm.get("context_adapter") else ""
        candidate = f" candidate={arm['candidate_id']}" if arm.get("candidate_id") else ""
        _human_print(
            f"  [{arm['arm_ordinal']}] {arm.get('name') or arm['arm_id']}  "
            f"{arm['completion_status']}{candidate}{adapter}"
        )

    replay = inspection.replay
    _human_print()
    _human_print("Evidence")
    if replay is None or not replay.evidence_bundles:
        _human_print("  -")
    elif replay is not None:
        for bundle in replay.evidence_bundles:
            _human_print(
                f"  {_string(bundle.metadata.get('phase')) or 'trial'}  {bundle.evidence_hash}  "
                f"candidates={len(bundle.candidates)}"
            )

    _human_print()
    _human_print("Reviews")
    if replay is None or not replay.reviews:
        _human_print("  -")
    elif replay is not None:
        for review in replay.reviews:
            evaluation = review.get("evaluation")
            first = ""
            order = evaluation.get("order") if isinstance(evaluation, Mapping) else None
            if isinstance(order, (list, tuple)) and order and isinstance(order[0], str):
                first = f" first={order[0]}"
            evaluator_id = _string(review.get("evaluator_id")) or "-"
            status = _string(review.get("status")) or "-"
            _human_print(f"  {evaluator_id}  {status}{first}")

    _human_print()
    _human_print("Resolution")
    resolution = replay.latest_resolution if replay is not None else None
    if resolution is None:
        _human_print("  -")
    else:
        target = resolution.candidate_id or "unresolved"
        _human_print(f"  {resolution.kind.value} -> {target}  id={resolution.resolution_id}")

    _human_print()
    _human_print("Delivery")
    if replay is None or replay.delivery is None:
        _human_print("  -")
    else:
        encoded = replay.delivery.get("delivery")
        encoded = encoded if isinstance(encoded, Mapping) else {}
        raw_files = encoded.get("files") or ()
        files = (
            ", ".join(item for item in raw_files if isinstance(item, str))
            if isinstance(raw_files, (list, tuple))
            else ""
        ) or "-"
        answer = "yes" if encoded.get("answer") is not None else "no"
        candidate_id = _string(replay.delivery.get("candidate_id")) or "-"
        _human_print(f"  {candidate_id}  files={files}  answer={answer}")

    _human_print()
    _human_print(f"Events: {len(inspection.events)}")
    if inspection.issues:
        _human_print()
        _human_print("Issues")
        for issue in inspection.issues:
            location = f" sequence={issue.sequence}" if issue.sequence is not None else ""
            _human_print(
                f"  {issue.code}{location}: {_safe_issue_dict(issue)['message']}"
            )


def _event_detail(event: Mapping[str, Any]) -> str:
    event_type = event.get("event_type")
    payload = event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    if event_type == "trial.started":
        return _one_line(
            _string(payload.get("task_name"))
            or _string(payload.get("brief"))
            or "ad-hoc",
            limit=70,
        )
    if event_type == "arm.completed":
        return " ".join(
            value
            for value in (
                _string(payload.get("phase")) or "trial",
                _string(payload.get("arm_id")) or "-",
                _string(payload.get("status")) or "-",
                _string(payload.get("candidate_id")) or "-",
            )
        )
    if event_type == "evidence.frozen":
        bundle = payload.get("bundle")
        bundle = bundle if isinstance(bundle, Mapping) else {}
        metadata = bundle.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        phase = _string(metadata.get("phase")) or "trial"
        evidence_hash = _string(bundle.get("evidence_hash")) or "-"
        return f"{phase} {evidence_hash}"
    if event_type == "review.recorded":
        evaluator_id = _string(payload.get("evaluator_id")) or "-"
        status = _string(payload.get("status")) or "-"
        return f"{evaluator_id} {status}"
    if event_type == "resolution.recorded":
        resolution = payload.get("resolution")
        resolution = resolution if isinstance(resolution, Mapping) else {}
        source = _string(resolution.get("source")) or "-"
        candidate_id = _string(resolution.get("candidate_id")) or "unresolved"
        return f"{source} -> {candidate_id}"
    if event_type == "delivery.completed":
        encoded = payload.get("delivery")
        encoded = encoded if isinstance(encoded, Mapping) else {}
        files = encoded.get("files") or ()
        file_count = len(files) if isinstance(files, (list, tuple)) else 0
        candidate_id = _string(payload.get("candidate_id")) or "-"
        return f"{candidate_id} files={file_count}"
    return ""


def render_replay(inspection: TrialInspection) -> None:
    _human_print(
        f"Trial {inspection.trial_id} replay  "
        f"status={inspection.status} integrity={inspection.integrity}"
    )
    rows = [["SEQ", "TIMESTAMP (UTC)", "EVENT", "DETAIL"]]
    for event in inspection.events:
        rows.append(
            [
                str(event.get("sequence", "-")),
                _format_time(event.get("timestamp")),
                str(event.get("event_type", "-")),
                _event_detail(event),
            ]
        )
    _render_table(rows)
    if inspection.issues:
        _human_print()
        _human_print("Issues")
        for issue in inspection.issues:
            _human_print(f"  {issue.code}: {issue.message}")


def _record_error(command: str, exc: RecordReadError, *, as_json: bool) -> int:
    exit_code = EXIT_CORRUPT if isinstance(exc, RecordCorruption) else EXIT_OPERATIONAL
    if isinstance(exc, RecordChanged):
        exit_code = EXIT_OPERATIONAL
    if as_json:
        _emit_json(
            _envelope(
                command,
                result="error",
                data=None,
                error=exc.to_dict(),
            )
        )
    else:
        _human_print(f"arity: {exc}", file=sys.stderr)
    return exit_code


def run_trials_command(args: Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    try:
        with open_record_reader() as reader:
            catalog = inspect_trials(reader)
    except RecordNotFound:
        # The configured default location not existing yet is the ordinary empty state.
        catalog = TrialCatalog(trials=())
    except RecordReadError as exc:
        return _record_error("trials", exc, as_json=as_json)

    result, exit_code = _catalog_result(catalog)
    warnings = [_safe_issue_dict(issue) for issue in catalog.issues]
    warnings.extend(
        _safe_issue_dict(issue)
        for trial in catalog.trials
        for issue in trial.issues
        if trial.integrity != "valid"
    )
    first_corrupt = next(
        (trial for trial in catalog.trials if trial.integrity == "corrupt"), None
    )
    error = _result_error(
        result,
        code="trial_catalog_corrupt",
        message=(
            _safe_issue_dict(first_corrupt.issues[0])["message"]
            if first_corrupt is not None and first_corrupt.issues
            else "the trial catalog contains invalid records"
        ),
        trial_id=None if first_corrupt is None else first_corrupt.trial_id,
    )
    if as_json:
        _emit_json(
            _envelope(
                "trials",
                result=result,
                data=catalog.to_dict(),
                error=error,
                warnings=warnings,
            )
        )
    else:
        render_catalog(catalog)
    return exit_code


def _load_trial(trial_id: str) -> TrialInspection:
    if not trial_id:
        raise TrialNotFound(trial_id)
    try:
        with open_record_reader() as reader:
            return inspect_trial(reader, trial_id)
    except RecordNotFound as exc:
        raise TrialNotFound(trial_id) from exc


def run_trial_command(args: Namespace) -> int:
    action = str(getattr(args, "trial_action", ""))
    command = f"trial.{action}"
    trial_id = str(getattr(args, "trial_id", ""))
    as_json = bool(getattr(args, "json", False))
    try:
        inspection = _load_trial(trial_id)
    except TrialNotFound as exc:
        if as_json:
            _emit_json(
                _envelope(command, result="error", data=None, error=exc.to_dict())
            )
        else:
            _human_print(f"arity: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    except RecordReadError as exc:
        return _record_error(command, exc, as_json=as_json)

    result, exit_code = _inspection_result(inspection)
    exposed_issues = (
        [_safe_issue_dict(issue) for issue in inspection.issues]
        if action == "show"
        else [issue.to_dict() for issue in inspection.issues]
    )
    error = _result_error(
        result,
        code="trial_corrupt",
        message=(
            exposed_issues[0]["message"]
            if exposed_issues
            else "the trial event stream is invalid"
        ),
        trial_id=inspection.trial_id,
    )
    warnings = (
        exposed_issues
        if inspection.integrity == "unsupported"
        else []
    )
    if as_json:
        data = (
            inspection_overview(inspection)
            if action == "show"
            else inspection.to_dict()
        )
        _emit_json(
            _envelope(
                command,
                result=result,
                data=data,
                error=error,
                warnings=warnings,
            )
        )
    elif action == "show":
        render_inspection(inspection)
    else:
        render_replay(inspection)
    return exit_code
