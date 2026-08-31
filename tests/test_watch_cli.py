"""Acceptance contract for the one-shot blind-safe watch command.

The Stage-2 public API is intentionally narrow::

    from arity.watch_terminal import render_watch_snapshot
    from arity.watch_cli import load_watch_model, run_watch_command

``render_watch_snapshot(model: WatchViewModel) -> str`` is a pure, canonical ASCII
renderer.  The public source seam is::

    load_watch_model(
        spec,
        *,
        selected_trial_id,
        projector,
        clock,
        reader_opener=open_record_reader,
        inspector=inspect_trials,
    ) -> WatchViewModel

It opens exactly one query-only reader, obtains one complete catalog snapshot, closes
the reader, samples the clock once, and then passes that catalog/read time to the
supplied projector.
``run_watch_command(args, *, clock=time.time, stdout=None, stderr=None) -> int`` takes an
``argparse.Namespace`` with ``trial_id``, ``ascii``, and ``no_motion`` fields, uses
that source seam, renders one snapshot, and returns the documented semantic exit code.
``None`` selects the current process stream dynamically; an explicitly supplied
text stream remains supported for embedding and tests.

The projected ``WatchViewModel`` carries two additional closed structural fields.
``catalog_integrity`` is ``valid``, ``partial``, or ``corrupt`` across the complete
catalog before its 256-row display cap; it determines semantic exit severity.
``selected_trial_omitted`` is a boolean used when an exact selected ID exists outside
that cap.  In that state ``selected_trial_number`` remains ``None`` so an unbounded
source rank can never enter the blind-safe model or terminal output.

Stage 2 deliberately has no interactive or terminal-capability mode.  The two flags
are accepted compatibility promises and cannot alter this already-ASCII snapshot.
"""

from __future__ import annotations

import argparse
import base64
import builtins
import io
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, cast

import pytest

import arity.auth as auth
import arity.handlers as handlers
import arity.inspection as inspection_module
import arity.inspection_cli as inspection_cli
import arity.runtime as runtime
import arity.stores.sqlite as sqlite_store_module
import arity.tools as tools
import arity.watch_cli as watch_cli
from arity.cli import main as cli_main
from arity.handlers import JsonlRecordStore
from arity.inspection import InspectionIssue, TrialCatalog, TrialInspection
from arity.record_readers import (
    RecordChanged,
    RecordCorruption,
    RecordNotFound,
    RecordReadError,
    StoreSpec,
)
from arity.stores.sqlite import SqliteRecordStore
from arity.trial_events import TrialEvent, TrialReplay
from arity.types import StoreRecord
from arity.watch_cli import load_watch_model, run_watch_command
from arity.watch_terminal import render_watch_snapshot
from arity.watch_view_model import (
    BoundedCount,
    WatchAgent,
    WatchIssue,
    WatchTrial,
    WatchTrialDetail,
    WatchViewModel,
    WatchProjector,
)


BLIND_LEAK_SENTINEL = "BLIND_LEAK_SENTINEL"
ADVERSARIAL_IDENTITY = (
    BLIND_LEAK_SENTINEL
    + "\x00\x1b[31m\r\n\t"
    + "\u202a\u202e\u2066\u2069"
    + " snowman=\u2603 han=\u96ea"
)
READ_AT = datetime(2030, 1, 2, 12, 4, 9).timestamp()
READ_TIME = "12:04:09"


def watch_args(
    trial_id: str | None = None,
    *,
    ascii: bool = False,
    no_motion: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        trial_id=trial_id,
        ascii=ascii,
        no_motion=no_motion,
    )


def count(value: int, *, more: bool = False) -> BoundedCount:
    return BoundedCount(value=value, more_omitted=more)


def detail(
    completions: tuple[bool, ...] = (),
    *,
    arms: int | None = None,
    arms_more: bool = False,
    completed: int | None = None,
    completed_more: bool = False,
    evidence: int = 0,
    evidence_more: bool = False,
    reviews: int = 0,
    reviews_more: bool = False,
    resolutions: int = 0,
    resolutions_more: bool = False,
    delivered: bool = False,
) -> WatchTrialDetail:
    resolved_arms = len(completions) if arms is None else arms
    resolved_completed = sum(completions) if completed is None else completed
    return WatchTrialDetail(
        agents=tuple(
            WatchAgent(position=index, completion_recorded=value)
            for index, value in enumerate(completions)
        ),
        arms=count(resolved_arms, more=arms_more),
        completed_agents=count(resolved_completed, more=completed_more),
        evidence=count(evidence, more=evidence_more),
        reviews=count(reviews, more=reviews_more),
        resolutions=count(resolutions, more=resolutions_more),
        delivery_recorded=delivered,
    )


def valid_trial(
    number: int,
    lifecycle: str = "started",
    *,
    trial_detail: WatchTrialDetail | None = None,
    selected: bool = False,
) -> WatchTrial:
    return WatchTrial(
        trial_number=number,
        integrity="valid",
        lifecycle=cast(Any, lifecycle),
        detail=detail() if trial_detail is None else trial_detail,
        issue=None,
        selected=selected,
    )


def partial_trial(
    number: int,
    *,
    lifecycle: str = "unknown",
    trial_detail: WatchTrialDetail | None = None,
    issue: str = "unsupported_event",
    selected: bool = False,
) -> WatchTrial:
    return WatchTrial(
        trial_number=number,
        integrity="partial",
        lifecycle=cast(Any, lifecycle),
        detail=trial_detail,
        issue=WatchIssue(cast(Any, issue)),
        selected=selected,
    )


def corrupt_trial(
    number: int,
    *,
    issue: str = "invalid_replay",
    selected: bool = False,
) -> WatchTrial:
    return WatchTrial(
        trial_number=number,
        integrity="corrupt",
        lifecycle="unknown",
        detail=None,
        issue=WatchIssue(cast(Any, issue)),
        selected=selected,
    )


def model(
    *trials: WatchTrial,
    backend: str = "jsonl",
    more_trials: bool = False,
    catalog_issues: tuple[WatchIssue, ...] = (),
    catalog_integrity: str | None = None,
    selected_number: int | None = None,
    selected_omitted: bool = False,
    requested_missing: bool = False,
    read_at: float = READ_AT,
) -> WatchViewModel:
    resolved_integrity = catalog_integrity
    if resolved_integrity is None:
        if catalog_issues or any(trial.integrity == "corrupt" for trial in trials):
            resolved_integrity = "corrupt"
        elif any(trial.integrity == "partial" for trial in trials):
            resolved_integrity = "partial"
        else:
            resolved_integrity = "valid"
    return WatchViewModel(
        backend=cast(Any, backend),
        read_at=float(read_at),
        trials=trials,
        more_trials_omitted=more_trials,
        catalog_issues=catalog_issues,
        catalog_integrity=cast(Any, resolved_integrity),
        selected_trial_number=selected_number,
        selected_trial_omitted=selected_omitted,
        requested_trial_missing=requested_missing,
    )


def assert_printable_ascii_snapshot(value: str) -> None:
    assert value.endswith("\n")
    assert not value.endswith("\n\n")
    assert "\r" not in value
    assert "\x1b" not in value
    assert all(character == "\n" or 0x20 <= ord(character) <= 0x7E for character in value)


def assert_blind_output(value: str, *extra_forbidden: str) -> None:
    encoded_fragments = (
        BLIND_LEAK_SENTINEL,
        base64.b64encode(BLIND_LEAK_SENTINEL.encode("ascii")).decode("ascii"),
        BLIND_LEAK_SENTINEL.encode("utf-8").hex(),
        urllib.parse.quote(BLIND_LEAK_SENTINEL, safe=""),
        *extra_forbidden,
    )
    lowered = value.lower()
    for fragment in encoded_fragments:
        assert fragment not in value
    for claim in (
        "running",
        "working",
        "thinking",
        "progress",
        "percent",
        "queued",
        "alive",
        "active",
        "%",
    ):
        assert claim not in lowered
    assert_printable_ascii_snapshot(value)


def run_direct(
    args: argparse.Namespace,
    *,
    clock: Callable[[], float] = lambda: READ_AT,
    stdout: io.StringIO | None = None,
    stderr: io.StringIO | None = None,
) -> tuple[int, str, str]:
    output = io.StringIO() if stdout is None else stdout
    errors = io.StringIO() if stderr is None else stderr
    code = run_watch_command(
        args,
        clock=clock,
        stdout=output,
        stderr=errors,
    )
    return code, output.getvalue(), errors.getvalue()


def started_replay(
    trial_id: str,
    *,
    timestamp: float = 1.0,
    arms: tuple[Mapping[str, Any], ...] = (),
    completed: tuple[Mapping[str, Any], ...] = (),
    hidden: Mapping[str, Any] | None = None,
) -> TrialReplay:
    payload: dict[str, Any] = dict(hidden or {})
    payload["arms"] = [dict(arm) for arm in arms]
    started = TrialEvent.create(
        trial_id=trial_id,
        sequence=1,
        event_type="trial.started",
        payload=payload,
        timestamp=timestamp,
        idempotency_key=(
            None if hidden is None else f"{BLIND_LEAK_SENTINEL}:idempotency"
        ),
    )
    return TrialReplay(
        trial_id=trial_id,
        events=(started,),
        started=started.payload,
        completed_arms=completed,
        evidence_bundles=(),
        reviews=(),
        evaluations=(),
        resolutions=(),
        resolution_sequences=(),
        delivery=None,
        unhandled_events=(),
    )


def valid_inspection(trial_id: str, *, timestamp: float = 1.0) -> TrialInspection:
    replay = started_replay(trial_id, timestamp=timestamp)
    return TrialInspection(
        trial_id=trial_id,
        integrity="valid",
        status="started",
        events=(),
        replay=replay,
        issues=(),
    )


def partial_inspection(
    trial_id: str,
    *,
    timestamp: float = 1.0,
) -> TrialInspection:
    replay = started_replay(
        trial_id,
        timestamp=timestamp,
        arms=({"arm_id": f"{trial_id}:arm", "arm_ordinal": 0},),
    )
    return TrialInspection(
        trial_id=trial_id,
        integrity="unsupported",
        status="started",
        events=(),
        replay=replay,
        issues=(
            InspectionIssue(
                code="unsupported_event",
                message=f"raw message {BLIND_LEAK_SENTINEL}",
                trial_id=trial_id,
                sequence=2,
                event_type=f"future.{BLIND_LEAK_SENTINEL}",
            ),
        ),
    )


def corrupt_inspection(trial_id: str) -> TrialInspection:
    return TrialInspection(
        trial_id=trial_id,
        integrity="corrupt",
        status="unknown",
        events=(),
        replay=None,
        issues=(
            InspectionIssue(
                code="invalid_replay",
                message=f"raw corruption {BLIND_LEAK_SENTINEL}",
                trial_id=trial_id,
            ),
        ),
    )


def capped_catalog_scenario(scenario: str) -> tuple[TrialCatalog, str]:
    prefix = f"raw-{BLIND_LEAK_SENTINEL}-{scenario}"
    offscreen_id = f"{prefix}-offscreen"
    if scenario == "all_valid":
        special = valid_inspection(offscreen_id, timestamp=0.0)
        visible = tuple(
            valid_inspection(f"{prefix}-{index:03d}", timestamp=float(index))
            for index in range(1, 257)
        )
        return TrialCatalog(trials=(special, *visible)), offscreen_id
    if scenario == "offscreen_partial":
        special = partial_inspection(offscreen_id, timestamp=0.0)
        visible = tuple(
            valid_inspection(f"{prefix}-{index:03d}", timestamp=float(index))
            for index in range(1, 257)
        )
        return TrialCatalog(trials=(special, *visible)), offscreen_id
    if scenario == "offscreen_corrupt":
        visible = tuple(
            valid_inspection(f"{prefix}-{index:03d}", timestamp=float(index))
            for index in range(1, 257)
        )
        return (
            TrialCatalog(trials=(*visible, corrupt_inspection(offscreen_id))),
            offscreen_id,
        )
    if scenario == "visible_partial_offscreen_corrupt":
        partial = partial_inspection(f"{prefix}-partial", timestamp=1000.0)
        visible = tuple(
            valid_inspection(f"{prefix}-{index:03d}", timestamp=float(index))
            for index in range(1, 256)
        )
        return (
            TrialCatalog(
                trials=(partial, *visible, corrupt_inspection(offscreen_id)),
            ),
            offscreen_id,
        )
    raise AssertionError(f"unknown capped catalog scenario: {scenario}")


class ReaderContext(AbstractContextManager[object]):
    def __init__(
        self,
        events: list[str],
        reader: object,
    ) -> None:
        self.events = events
        self.reader = reader

    def __enter__(self) -> object:
        self.events.append("reader_enter")
        return self.reader

    def __exit__(self, *exc_info: object) -> None:
        self.events.append("reader_close")


def install_catalog_source(
    monkeypatch: pytest.MonkeyPatch,
    catalog: TrialCatalog,
    *,
    backend: str = "jsonl",
    inspect_error: RecordReadError | None = None,
) -> list[str]:
    events: list[str] = []
    spec = StoreSpec(cast(Any, backend), Path(BLIND_LEAK_SENTINEL) / "records")
    reader = object()

    def configured() -> StoreSpec:
        events.append("configured")
        return spec

    def opened(actual: StoreSpec | None = None) -> ReaderContext:
        assert actual is spec
        events.append("reader_open")
        return ReaderContext(events, reader)

    def inspected(actual: object) -> TrialCatalog:
        assert actual is reader
        events.append("inspect_trials")
        if inspect_error is not None:
            raise inspect_error
        return catalog

    monkeypatch.setattr(watch_cli, "configured_store_spec", configured)
    monkeypatch.setattr(watch_cli, "open_record_reader", opened)
    monkeypatch.setattr(watch_cli, "inspect_trials", inspected)
    return events


def tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def persist_events(
    root: Path,
    backend: str,
    events: tuple[TrialEvent, ...],
) -> Path:
    if backend == "jsonl":
        path = root / ".gorkbot" / "records"
        store = JsonlRecordStore(path)
    else:
        path = root / ".gorkbot" / "records.db"
        store = SqliteRecordStore(path)
    for event in events:
        store.append(StoreRecord(kind="trial_event", record=event.to_dict()))
    close = getattr(store, "close", None)
    if close is not None:
        close()
    return path


def sentinel_events(trial_id: str) -> tuple[TrialEvent, ...]:
    encoded = base64.b64encode(BLIND_LEAK_SENTINEL.encode("ascii")).decode("ascii")
    hexadecimal = BLIND_LEAK_SENTINEL.encode("utf-8").hex()
    escaped = urllib.parse.quote(BLIND_LEAK_SENTINEL, safe="")
    arm_id = f"{BLIND_LEAK_SENTINEL}:arm"
    hidden = {
        "task_id": BLIND_LEAK_SENTINEL,
        "task_name": BLIND_LEAK_SENTINEL,
        "brief": BLIND_LEAK_SENTINEL,
        "role": BLIND_LEAK_SENTINEL,
        "requested_arity": 1,
        "resolved_arity": 1,
        "evaluator_ids": [BLIND_LEAK_SENTINEL],
        "hidden_test_hashes": {BLIND_LEAK_SENTINEL: BLIND_LEAK_SENTINEL},
        "arms": [
            {
                "arm_id": arm_id,
                "arm_ordinal": -(10**100),
                "name": BLIND_LEAK_SENTINEL,
                "signature": BLIND_LEAK_SENTINEL,
                "model": BLIND_LEAK_SENTINEL,
                "provider": BLIND_LEAK_SENTINEL,
                "role": BLIND_LEAK_SENTINEL,
                "harness": BLIND_LEAK_SENTINEL,
                "tool_runner": BLIND_LEAK_SENTINEL,
                "skills": [BLIND_LEAK_SENTINEL],
                "context": BLIND_LEAK_SENTINEL,
                "context_adapter": BLIND_LEAK_SENTINEL,
            }
        ],
        "nested": {
            "identity": BLIND_LEAK_SENTINEL,
            "output": BLIND_LEAK_SENTINEL,
            "artifact": {"path": BLIND_LEAK_SENTINEL, "body": encoded},
            "delivery": {"files": [BLIND_LEAK_SENTINEL], "answer": hexadecimal},
            "credential": escaped,
        },
    }
    started = TrialEvent.create(
        trial_id=trial_id,
        sequence=1,
        event_type="trial.started",
        payload=hidden,
        timestamp=1.0,
        idempotency_key=f"{BLIND_LEAK_SENTINEL}:idempotency",
    )
    future = TrialEvent.create(
        trial_id=trial_id,
        sequence=2,
        event_type=f"future.{BLIND_LEAK_SENTINEL}",
        payload={
            "status": BLIND_LEAK_SENTINEL,
            "candidate_id": BLIND_LEAK_SENTINEL,
            "evaluator_id": BLIND_LEAK_SENTINEL,
            "resolution_id": BLIND_LEAK_SENTINEL,
            "output": BLIND_LEAK_SENTINEL,
            "encoded": [encoded, hexadecimal, escaped],
            "recursive": hidden,
        },
        timestamp=2.0,
        idempotency_key=f"{BLIND_LEAK_SENTINEL}:future-key",
    )
    return started, future


def hostile_identity_events(trial_id: str) -> tuple[TrialEvent, ...]:
    """Put terminal controls, bidi marks, and Unicode in every identity family."""
    arm_id = f"arm:{ADVERSARIAL_IDENTITY}"
    started = TrialEvent.create(
        trial_id=trial_id,
        sequence=1,
        event_type="trial.started",
        payload={
            "task_id": ADVERSARIAL_IDENTITY,
            "task_name": ADVERSARIAL_IDENTITY,
            "brief": ADVERSARIAL_IDENTITY,
            "role": ADVERSARIAL_IDENTITY,
            "evaluator_ids": [ADVERSARIAL_IDENTITY],
            "hidden_test_hashes": {
                ADVERSARIAL_IDENTITY: ADVERSARIAL_IDENTITY,
            },
            "arms": [
                {
                    "arm_id": arm_id,
                    "arm_ordinal": 10**100,
                    "name": ADVERSARIAL_IDENTITY,
                    "signature": ADVERSARIAL_IDENTITY,
                    "model": ADVERSARIAL_IDENTITY,
                    "provider": ADVERSARIAL_IDENTITY,
                    "role": ADVERSARIAL_IDENTITY,
                    "harness": ADVERSARIAL_IDENTITY,
                    "tool_runner": ADVERSARIAL_IDENTITY,
                    "skills": [ADVERSARIAL_IDENTITY],
                    "context": ADVERSARIAL_IDENTITY,
                    "context_adapter": ADVERSARIAL_IDENTITY,
                }
            ],
        },
        timestamp=1.0,
        idempotency_key=f"start:{ADVERSARIAL_IDENTITY}",
    )
    future = TrialEvent.create(
        trial_id=trial_id,
        sequence=2,
        event_type=f"future.{ADVERSARIAL_IDENTITY}",
        payload={
            "arm_id": arm_id,
            "candidate_id": ADVERSARIAL_IDENTITY,
            "evaluator_id": ADVERSARIAL_IDENTITY,
            "resolution_id": ADVERSARIAL_IDENTITY,
            "status": ADVERSARIAL_IDENTITY,
            "output": ADVERSARIAL_IDENTITY,
            "artifact": {
                "path": ADVERSARIAL_IDENTITY,
                "body": ADVERSARIAL_IDENTITY,
            },
            "delivery": {
                "files": [ADVERSARIAL_IDENTITY],
                "answer": ADVERSARIAL_IDENTITY,
            },
        },
        timestamp=2.0,
        idempotency_key=f"future:{ADVERSARIAL_IDENTITY}",
    )
    return started, future


def test_empty_renderer_is_exact_and_independent_of_backend_and_clock() -> None:
    for backend in ("jsonl", "sqlite"):
        rendered = render_watch_snapshot(
            model(backend=backend, read_at=READ_AT + 1000.0),
        )
        assert rendered == "No persisted trials.\n"
        assert_printable_ascii_snapshot(rendered)


@pytest.mark.parametrize("read_at", [1e308, -1e308])
def test_unrepresentable_finite_read_time_uses_fixed_unknown_time(
    read_at: float,
) -> None:
    """An unrepresentable local time stays unknown instead of inventing midnight."""
    rendered = render_watch_snapshot(
        model(
            valid_trial(1),
            read_at=read_at,
        )
    )

    assert rendered == (
        "arity watch | jsonl | 1 trial | read ??:??:??\n"
        "  Trial 1 | started | valid | completions 0/0\n"
    )
    assert "read 00:00:00" not in rendered
    assert_printable_ascii_snapshot(rendered)


def test_renderer_has_one_canonical_line_for_every_lifecycle() -> None:
    rendered = render_watch_snapshot(
        model(
            valid_trial(1, "started", trial_detail=detail((False, False))),
            valid_trial(2, "evidenced", trial_detail=detail((True, False), evidence=1)),
            valid_trial(
                3,
                "unresolved",
                trial_detail=detail((True, True), evidence=1, reviews=1, resolutions=1),
            ),
            valid_trial(
                4,
                "resolved",
                trial_detail=detail((True, True), evidence=1, reviews=1, resolutions=1),
            ),
            valid_trial(
                5,
                "delivered",
                trial_detail=detail(
                    (True, True), evidence=1, reviews=1, resolutions=1, delivered=True,
                ),
            ),
            backend="sqlite",
        )
    )

    assert rendered == (
        f"arity watch | sqlite | 5 trials | read {READ_TIME}\n"
        "  Trial 1 | started | valid | completions 0/2\n"
        "  Trial 2 | evidenced | valid | completions 1/2\n"
        "  Trial 3 | unresolved | valid | completions 2/2\n"
        "  Trial 4 | resolved | valid | completions 2/2\n"
        "  Trial 5 | delivered | valid | completions 2/2\n"
    )
    assert_blind_output(rendered)


def test_renderer_expands_only_the_selected_verified_trial() -> None:
    selected_detail = detail((True, False), evidence=1)
    rendered = render_watch_snapshot(
        model(
            valid_trial(1, "started", trial_detail=detail((False,))),
            partial_trial(
                2,
                lifecycle="evidenced",
                trial_detail=selected_detail,
                selected=True,
            ),
            selected_number=2,
        )
    )

    assert rendered == (
        f"arity watch | jsonl | 2 trials | read {READ_TIME}\n"
        "  Trial 1 | started | valid | completions 0/1\n"
        "> Trial 2 | evidenced | partial | completions 1/2\n"
        "    Agent A | completion recorded\n"
        "    Agent B | no completion recorded\n"
        "    issue unsupported_event\n"
        "      The trial contains an event type this version does not understand.\n"
        "selected: Trial 2\n"
        "  evidence 1 | reviews 0 | resolutions 0 | delivery no\n"
    )
    assert "Agent" not in rendered.split("> Trial 2", 1)[0]
    assert_blind_output(rendered)


def test_renderer_suppresses_detail_for_unknown_partial_and_corrupt_trials() -> None:
    rendered = render_watch_snapshot(
        model(
            partial_trial(
                1,
                issue="unsupported_event_schema",
            ),
            corrupt_trial(2, selected=True),
            selected_number=2,
        )
    )

    assert rendered == (
        f"arity watch | jsonl | 2 trials | read {READ_TIME}\n"
        "  Trial 1 | unknown | partial | details unavailable\n"
        "    issue unsupported_event_schema\n"
        "      The trial contains a newer event schema.\n"
        "> Trial 2 | unknown | corrupt | details unavailable\n"
        "    issue invalid_replay\n"
        "      The trial journal violates lifecycle invariants.\n"
        "selected: Trial 2 | details unavailable\n"
    )
    assert "Agent" not in rendered
    assert_blind_output(rendered)


def test_renderer_shows_catalog_issues_without_untrusted_messages() -> None:
    rendered = render_watch_snapshot(
        model(
            catalog_issues=(
                WatchIssue("invalid_record"),
                WatchIssue("orphan_event"),
            ),
        )
    )

    assert rendered == (
        f"arity watch | jsonl | 0 trials | read {READ_TIME}\n"
        "  issue invalid_record\n"
        "    A persisted trial record is not valid event data.\n"
        "  issue orphan_event\n"
        "    A persisted event has no usable trial identity.\n"
    )
    assert_blind_output(rendered)


def test_renderer_uses_only_bounded_omission_wording() -> None:
    bounded = detail(
        (True, False),
        arms=256,
        arms_more=True,
        completed=256,
        completed_more=True,
        evidence=256,
        evidence_more=True,
        reviews=256,
        reviews_more=True,
        resolutions=256,
        resolutions_more=True,
        delivered=True,
    )
    rendered = render_watch_snapshot(
        model(
            valid_trial(9, "delivered", trial_detail=bounded, selected=True),
            more_trials=True,
            selected_number=9,
        )
    )

    assert rendered == (
        f"arity watch | jsonl | 1 trial | read {READ_TIME}\n"
        "> Trial 9 | delivered | valid | "
        "completions 256 (more omitted)/256 (more omitted)\n"
        "    Agent A | completion recorded\n"
        "    Agent B | no completion recorded\n"
        "    more agents omitted\n"
        "  more trials omitted\n"
        "selected: Trial 9\n"
        "  evidence 256 (more omitted) | reviews 256 (more omitted) | "
        "resolutions 256 (more omitted) | delivery yes\n"
    )
    for forbidden_total in ("257", "258", "1000"):
        assert forbidden_total not in rendered
    assert_blind_output(rendered)


def test_renderer_reports_offscreen_selection_without_exposing_identity() -> None:
    rendered = render_watch_snapshot(
        model(
            valid_trial(1, trial_detail=detail((False,))),
            more_trials=True,
            selected_omitted=True,
        )
    )

    assert rendered == (
        f"arity watch | jsonl | 1 trial | read {READ_TIME}\n"
        "  Trial 1 | started | valid | completions 0/1\n"
        "  more trials omitted\n"
        "selected: omitted trial | details unavailable\n"
    )
    assert "257" not in rendered
    assert_blind_output(rendered)


class UnsafeRendererInput:
    def __repr__(self) -> str:
        return BLIND_LEAK_SENTINEL


class ForgedWatchViewModel(WatchViewModel):
    pass


class LeakyIntegrity(str):
    def __repr__(self) -> str:
        return BLIND_LEAK_SENTINEL


@pytest.mark.parametrize(
    "unsafe_integrity",
    ["", "unsupported", "VALID", LeakyIntegrity("valid")],
)
def test_catalog_integrity_is_a_closed_plain_string(
    unsafe_integrity: str,
) -> None:
    with pytest.raises((TypeError, ValueError)) as captured:
        model(catalog_integrity=unsafe_integrity)
    assert BLIND_LEAK_SENTINEL not in str(captured.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"selected_number": 1, "selected_omitted": True},
        {"selected_omitted": True, "requested_missing": True},
        {"selected_omitted": cast(Any, 1)},
    ],
)
def test_selected_trial_omitted_is_a_closed_exclusive_boolean(
    overrides: Mapping[str, Any],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        model(**overrides)


@pytest.mark.parametrize(
    "unsafe",
    [
        UnsafeRendererInput(),
        {},
        TrialCatalog(trials=()),
        ForgedWatchViewModel(
            backend="jsonl",
            read_at=READ_AT,
            trials=(),
            more_trials_omitted=False,
            catalog_integrity="valid",
            selected_trial_omitted=False,
        ),
    ],
)
def test_renderer_accepts_only_an_exact_watch_view_model(unsafe: object) -> None:
    with pytest.raises(TypeError) as captured:
        render_watch_snapshot(cast(Any, unsafe))
    assert BLIND_LEAK_SENTINEL not in str(captured.value)


def test_renderer_does_not_query_terminal_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("one-shot renderer attempted terminal capability I/O")

    monkeypatch.setattr(os, "get_terminal_size", forbidden)
    monkeypatch.setattr(shutil, "get_terminal_size", forbidden)
    # Replace the streams instead of mutating TextIOWrapper methods, which are
    # read-only on CPython and differ under pytest's capture implementation.
    monkeypatch.setattr(sys, "stdout", GuardedOutput())
    monkeypatch.setattr(sys, "stderr", GuardedOutput())
    for name in ("get_terminal_size", "isatty", "fileno"):
        if hasattr(watch_cli, name):
            monkeypatch.setattr(watch_cli, name, forbidden)

    rendered = render_watch_snapshot(
        model(valid_trial(1, trial_detail=detail((False,))))
    )
    assert rendered.startswith("arity watch | jsonl | 1 trial")
    assert_printable_ascii_snapshot(rendered)


def test_load_watch_model_is_one_injected_reader_and_samples_clock_after_close() -> None:
    events: list[str] = []
    raw_id = f"raw-{BLIND_LEAK_SENTINEL}"
    source_catalog = TrialCatalog(trials=(valid_inspection(raw_id),))
    spec = StoreSpec("sqlite", Path(BLIND_LEAK_SENTINEL) / "records.db")
    reader = object()
    projector = WatchProjector()

    def reader_opener(actual: StoreSpec | None = None) -> ReaderContext:
        assert actual is spec
        events.append("reader_open")
        return ReaderContext(events, reader)

    def inspector(actual: object) -> TrialCatalog:
        assert actual is reader
        events.append("inspect_trials")
        return source_catalog

    def clock() -> float:
        events.append("clock")
        return READ_AT

    loaded = load_watch_model(
        spec,
        selected_trial_id=raw_id,
        projector=projector,
        clock=clock,
        reader_opener=reader_opener,
        inspector=inspector,
    )

    assert type(loaded) is WatchViewModel
    assert loaded.backend == "sqlite"
    assert loaded.read_at == READ_AT
    assert loaded.catalog_integrity == "valid"
    assert loaded.selected_trial_number == 1
    assert loaded.selected_trial_omitted is False
    assert loaded.requested_trial_missing is False
    assert events == [
        "reader_open",
        "reader_enter",
        "inspect_trials",
        "reader_close",
        "clock",
    ]
    assert BLIND_LEAK_SENTINEL not in repr(loaded)


def test_load_watch_model_closes_and_propagates_typed_failure_before_clock() -> None:
    events: list[str] = []
    spec = StoreSpec("jsonl", Path(BLIND_LEAK_SENTINEL) / "records")
    reader = object()
    failure = RecordCorruption(
        f"corrupt {BLIND_LEAK_SENTINEL}",
        path=Path(BLIND_LEAK_SENTINEL),
    )

    def reader_opener(actual: StoreSpec | None = None) -> ReaderContext:
        assert actual is spec
        events.append("reader_open")
        return ReaderContext(events, reader)

    def inspector(actual: object) -> TrialCatalog:
        assert actual is reader
        events.append("inspect_trials")
        raise failure

    def clock() -> float:
        events.append("clock")
        return READ_AT

    with pytest.raises(RecordCorruption) as captured:
        load_watch_model(
            spec,
            selected_trial_id=None,
            projector=WatchProjector(),
            clock=clock,
            reader_opener=reader_opener,
            inspector=inspector,
        )

    assert captured.value is failure
    assert events == [
        "reader_open",
        "reader_enter",
        "inspect_trials",
        "reader_close",
    ]


@pytest.mark.parametrize(
    ("scenario", "expected_integrity"),
    [
        ("all_valid", "valid"),
        ("offscreen_partial", "partial"),
        ("offscreen_corrupt", "corrupt"),
        ("visible_partial_offscreen_corrupt", "corrupt"),
    ],
)
def test_load_watch_model_keeps_full_catalog_integrity_before_capping(
    scenario: str,
    expected_integrity: str,
) -> None:
    catalog, offscreen_id = capped_catalog_scenario(scenario)
    events: list[str] = []
    reader = object()
    spec = StoreSpec("jsonl", Path(BLIND_LEAK_SENTINEL) / "records")

    def reader_opener(actual: StoreSpec | None = None) -> ReaderContext:
        assert actual is spec
        events.append("reader_open")
        return ReaderContext(events, reader)

    def inspector(actual: object) -> TrialCatalog:
        assert actual is reader
        events.append("inspect_trials")
        return catalog

    loaded = load_watch_model(
        spec,
        selected_trial_id=(offscreen_id if scenario == "all_valid" else None),
        projector=WatchProjector(),
        clock=lambda: READ_AT,
        reader_opener=reader_opener,
        inspector=inspector,
    )

    assert loaded.catalog_integrity == expected_integrity
    assert len(loaded.trials) == 256
    assert loaded.more_trials_omitted is True
    if scenario == "all_valid":
        assert loaded.selected_trial_number is None
        assert loaded.selected_trial_omitted is True
        assert loaded.requested_trial_missing is False
        assert "257" not in repr(loaded)
    else:
        assert loaded.selected_trial_omitted is False
    assert events.count("inspect_trials") == 1
    assert events.count("reader_close") == 1
    assert BLIND_LEAK_SENTINEL not in repr(loaded)


def test_source_takes_one_full_catalog_snapshot_closes_then_reads_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = install_catalog_source(monkeypatch, TrialCatalog(trials=()))

    def clock() -> float:
        events.append("clock")
        return READ_AT

    code, stdout, stderr = run_direct(watch_args(), clock=clock)

    assert (code, stdout, stderr) == (0, "No persisted trials.\n", "")
    assert events == [
        "configured",
        "reader_open",
        "reader_enter",
        "inspect_trials",
        "reader_close",
        "clock",
    ]


def test_source_never_implicitly_selects_a_trial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_id = f"raw-{BLIND_LEAK_SENTINEL}"
    install_catalog_source(
        monkeypatch,
        TrialCatalog(trials=(valid_inspection(raw_id),)),
    )

    code, stdout, stderr = run_direct(watch_args())

    assert code == 0
    assert stderr == ""
    assert stdout == (
        f"arity watch | jsonl | 1 trial | read {READ_TIME}\n"
        "  Trial 1 | started | valid | completions 0/0\n"
    )
    assert ">" not in stdout
    assert "selected:" not in stdout
    assert_blind_output(stdout, raw_id)


def test_explicit_selection_still_uses_the_one_full_capped_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_id = f"raw-selected-{BLIND_LEAK_SENTINEL}"
    trials = tuple(
        valid_inspection(
            selected_id if index == 0 else f"raw-{index:03d}",
            timestamp=float(index),
        )
        for index in range(257)
    )
    events = install_catalog_source(monkeypatch, TrialCatalog(trials=trials))

    def forbidden_inspect_trial(*args: object, **kwargs: object) -> Any:
        raise AssertionError("watch must not replace the full catalog with inspect_trial")

    monkeypatch.setattr(inspection_module, "inspect_trial", forbidden_inspect_trial)
    if hasattr(watch_cli, "inspect_trial"):
        monkeypatch.setattr(watch_cli, "inspect_trial", forbidden_inspect_trial)

    code, stdout, stderr = run_direct(watch_args(selected_id))

    assert code == 0
    assert stderr == ""
    assert events.count("inspect_trials") == 1
    assert events.count("reader_close") == 1
    assert "  more trials omitted\n" in stdout
    assert "selected: omitted trial | details unavailable\n" in stdout
    assert stdout.count("Trial ") == 256
    assert "257" not in stdout
    assert_blind_output(stdout, selected_id)


@pytest.mark.parametrize(
    ("scenario", "expected_code", "visible_partial"),
    [
        ("all_valid", 0, False),
        ("offscreen_partial", 4, False),
        ("offscreen_corrupt", 5, False),
        ("visible_partial_offscreen_corrupt", 5, True),
    ],
)
def test_full_catalog_integrity_survives_the_256_row_display_cap(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_code: int,
    visible_partial: bool,
) -> None:
    catalog, offscreen_id = capped_catalog_scenario(scenario)
    install_catalog_source(monkeypatch, catalog)

    code, stdout, stderr = run_direct(watch_args())

    assert code == expected_code
    assert stderr == ""
    assert stdout.startswith(
        f"arity watch | jsonl | 256 trials | read {READ_TIME}\n"
    )
    assert "  more trials omitted\n" in stdout
    assert stdout.count("Trial ") == 256
    assert ("| partial |" in stdout) is visible_partial
    assert "| corrupt |" not in stdout
    assert "257" not in stdout
    assert_blind_output(stdout, offscreen_id)


@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        ("offscreen_partial", 4),
        ("offscreen_corrupt", 5),
    ],
)
def test_selected_offscreen_failure_keeps_aggregate_exit_and_bounded_label(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_code: int,
) -> None:
    catalog, offscreen_id = capped_catalog_scenario(scenario)
    install_catalog_source(monkeypatch, catalog)

    code, stdout, stderr = run_direct(watch_args(offscreen_id))

    assert code == expected_code
    assert stderr == ""
    assert "selected: omitted trial | details unavailable\n" in stdout
    assert "| partial |" not in stdout
    assert "| corrupt |" not in stdout
    assert "257" not in stdout
    assert_blind_output(stdout, offscreen_id)


@pytest.mark.parametrize(
    ("catalog", "expected_code"),
    [
        (TrialCatalog(trials=(partial_inspection("raw-partial"),)), 4),
        (TrialCatalog(trials=(corrupt_inspection("raw-corrupt"),)), 5),
        (
            TrialCatalog(
                trials=(),
                issues=(
                    InspectionIssue(
                        code="orphan_event",
                        message=BLIND_LEAK_SENTINEL,
                        trial_id=BLIND_LEAK_SENTINEL,
                    ),
                ),
            ),
            5,
        ),
        (
            TrialCatalog(
                trials=(
                    partial_inspection("raw-partial"),
                    corrupt_inspection("raw-corrupt"),
                ),
            ),
            5,
        ),
    ],
)
def test_logical_partial_and_corrupt_snapshots_render_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
    catalog: TrialCatalog,
    expected_code: int,
) -> None:
    install_catalog_source(monkeypatch, catalog)

    code, stdout, stderr = run_direct(watch_args())

    assert code == expected_code
    assert stderr == ""
    assert stdout.startswith("arity watch | jsonl |")
    if expected_code == 4:
        assert "| partial |" in stdout
    else:
        assert "| corrupt |" in stdout or "issue orphan_event" in stdout
    assert_blind_output(stdout)


def test_requested_missing_beats_other_logical_partial_and_corrupt_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = f"missing-{BLIND_LEAK_SENTINEL}"
    install_catalog_source(
        monkeypatch,
        TrialCatalog(
            trials=(
                partial_inspection("raw-partial"),
                corrupt_inspection("raw-corrupt"),
            ),
            issues=(
                InspectionIssue(
                    code="orphan_event",
                    message=BLIND_LEAK_SENTINEL,
                    trial_id=BLIND_LEAK_SENTINEL,
                ),
            ),
        ),
    )

    code, stdout, stderr = run_direct(watch_args(requested))

    assert (code, stdout, stderr) == (3, "", "arity: trial_not_found\n")
    assert_blind_output(stderr, requested)


@pytest.mark.parametrize(
    ("other", "expected_code", "expected_integrity"),
    [
        (partial_inspection("raw-other-partial"), 4, "partial"),
        (corrupt_inspection("raw-other-corrupt"), 5, "corrupt"),
    ],
)
def test_selected_valid_trial_does_not_hide_other_logical_failure(
    monkeypatch: pytest.MonkeyPatch,
    other: TrialInspection,
    expected_code: int,
    expected_integrity: str,
) -> None:
    selected = f"selected-valid-{BLIND_LEAK_SENTINEL}"
    install_catalog_source(
        monkeypatch,
        TrialCatalog(
            trials=(
                valid_inspection(selected, timestamp=10.0),
                other,
            ),
        ),
    )

    code, stdout, stderr = run_direct(watch_args(selected))

    assert code == expected_code
    assert stderr == ""
    assert "> Trial 1 | started | valid |" in stdout
    assert f"| {expected_integrity} |" in stdout
    assert "selected: Trial 1" in stdout
    assert_blind_output(stdout, selected)


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_stderr"),
    [
        (
            RecordChanged(
                f"changed {BLIND_LEAK_SENTINEL}",
                path=Path(BLIND_LEAK_SENTINEL),
            ),
            1,
            "arity: record_store_changed\n",
        ),
        (
            RecordReadError(
                f"failed {BLIND_LEAK_SENTINEL}",
                path=Path(BLIND_LEAK_SENTINEL),
            ),
            1,
            "arity: record_read_error\n",
        ),
        (
            RecordCorruption(
                f"corrupt {BLIND_LEAK_SENTINEL}",
                path=Path(BLIND_LEAK_SENTINEL),
            ),
            5,
            "arity: record_store_corrupt\n",
        ),
    ],
)
def test_typed_physical_failures_close_once_and_emit_only_canned_stderr(
    monkeypatch: pytest.MonkeyPatch,
    error: RecordReadError,
    expected_code: int,
    expected_stderr: str,
) -> None:
    events = install_catalog_source(
        monkeypatch,
        TrialCatalog(trials=()),
        inspect_error=error,
    )
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return READ_AT

    code, stdout, stderr = run_direct(watch_args(), clock=clock)

    assert (code, stdout, stderr) == (expected_code, "", expected_stderr)
    assert events.count("reader_enter") == 1
    assert events.count("inspect_trials") == 1
    assert events.count("reader_close") == 1
    assert clock_calls == 0
    assert_blind_output(stderr)


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_stderr"),
    [
        (
            RecordChanged(
                f"changed {BLIND_LEAK_SENTINEL}",
                path=Path(BLIND_LEAK_SENTINEL),
            ),
            1,
            "arity: record_store_changed\n",
        ),
        (
            RecordReadError(
                f"failed {BLIND_LEAK_SENTINEL}",
                path=Path(BLIND_LEAK_SENTINEL),
            ),
            1,
            "arity: record_read_error\n",
        ),
        (
            RecordCorruption(
                f"corrupt {BLIND_LEAK_SENTINEL}",
                path=Path(BLIND_LEAK_SENTINEL),
            ),
            5,
            "arity: record_store_corrupt\n",
        ),
    ],
)
def test_physical_failure_keeps_its_code_when_a_trial_was_requested(
    monkeypatch: pytest.MonkeyPatch,
    error: RecordReadError,
    expected_code: int,
    expected_stderr: str,
) -> None:
    requested = f"requested-{BLIND_LEAK_SENTINEL}"
    events = install_catalog_source(
        monkeypatch,
        TrialCatalog(trials=()),
        inspect_error=error,
    )

    code, stdout, stderr = run_direct(watch_args(requested))

    assert (code, stdout, stderr) == (expected_code, "", expected_stderr)
    assert events.count("reader_close") == 1
    assert_blind_output(stderr, requested)


def test_missing_selected_trial_is_fixed_exit_three_without_raw_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_id = f"missing-{BLIND_LEAK_SENTINEL}"
    install_catalog_source(monkeypatch, TrialCatalog(trials=()))

    code, stdout, stderr = run_direct(watch_args(raw_id))

    assert (code, stdout, stderr) == (3, "", "arity: trial_not_found\n")
    assert raw_id not in stderr
    assert_blind_output(stderr)


def test_record_not_found_is_empty_without_selection_and_missing_with_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = StoreSpec("jsonl", Path(BLIND_LEAK_SENTINEL) / "records")

    def configured() -> StoreSpec:
        return spec

    def missing(actual: StoreSpec | None = None) -> AbstractContextManager[object]:
        assert actual is spec
        raise RecordNotFound(
            f"missing {BLIND_LEAK_SENTINEL}",
            path=Path(BLIND_LEAK_SENTINEL),
        )

    monkeypatch.setattr(watch_cli, "configured_store_spec", configured)
    monkeypatch.setattr(watch_cli, "open_record_reader", missing)

    assert run_direct(watch_args()) == (0, "No persisted trials.\n", "")
    assert run_direct(watch_args(f"raw-{BLIND_LEAK_SENTINEL}")) == (
        3,
        "",
        "arity: trial_not_found\n",
    )


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_real_readers_render_selected_snapshot_without_mutating_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    raw_id = f"raw-{BLIND_LEAK_SENTINEL}"
    arm_id = f"arm-{BLIND_LEAK_SENTINEL}"
    event = TrialEvent.create(
        trial_id=raw_id,
        sequence=1,
        event_type="trial.started",
        payload={
            "task_id": BLIND_LEAK_SENTINEL,
            "task_name": BLIND_LEAK_SENTINEL,
            "brief": BLIND_LEAK_SENTINEL,
            "role": BLIND_LEAK_SENTINEL,
            "arms": [
                {
                    "arm_id": arm_id,
                    "arm_ordinal": -(10**100),
                    "name": BLIND_LEAK_SENTINEL,
                    "model": BLIND_LEAK_SENTINEL,
                    "provider": BLIND_LEAK_SENTINEL,
                    "role": BLIND_LEAK_SENTINEL,
                }
            ],
        },
        timestamp=1.0,
        idempotency_key=f"key-{BLIND_LEAK_SENTINEL}",
    )
    persist_events(tmp_path, backend, (event,))
    before = tree_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", backend)

    code, stdout, stderr = run_direct(watch_args(raw_id))

    assert code == 0
    assert stderr == ""
    assert stdout == (
        f"arity watch | {backend} | 1 trial | read {READ_TIME}\n"
        "> Trial 1 | started | valid | completions 0/1\n"
        "    Agent A | no completion recorded\n"
        "selected: Trial 1\n"
        "  evidence 0 | reviews 0 | resolutions 0 | delivery no\n"
    )
    assert tree_snapshot(tmp_path) == before
    assert_blind_output(stdout, raw_id, arm_id, str(tmp_path))


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_real_missing_store_never_creates_dot_gorkbot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", backend)
    missing_tree = tmp_path / ".gorkbot"

    assert run_direct(watch_args()) == (0, "No persisted trials.\n", "")
    assert not missing_tree.exists()

    raw_id = f"missing-{BLIND_LEAK_SENTINEL}"
    assert run_direct(watch_args(raw_id)) == (
        3,
        "",
        "arity: trial_not_found\n",
    )
    assert not missing_tree.exists()


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_recursive_persisted_sentinel_and_encodings_are_blind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    raw_id = f"raw-{BLIND_LEAK_SENTINEL}"
    persist_events(tmp_path, backend, sentinel_events(raw_id))
    before = tree_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", backend)

    code, stdout, stderr = run_direct(watch_args(raw_id))

    assert code == 4
    assert stderr == ""
    assert "| partial |" in stdout
    assert "issue unsupported_event" in stdout
    assert "Agent A | no completion recorded" in stdout
    assert tree_snapshot(tmp_path) == before
    assert_blind_output(stdout, raw_id, str(tmp_path))


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_control_escape_bidi_and_non_ascii_identities_cannot_reach_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    raw_id = f"trial:{ADVERSARIAL_IDENTITY}"
    persist_events(tmp_path, backend, hostile_identity_events(raw_id))
    before = tree_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", backend)

    code, stdout, stderr = run_direct(
        watch_args(raw_id, ascii=True, no_motion=True),
    )

    assert code == 4
    assert stderr == ""
    assert "| partial |" in stdout
    assert "issue unsupported_event" in stdout
    assert "Agent A | no completion recorded" in stdout
    assert tree_snapshot(tmp_path) == before
    assert ADVERSARIAL_IDENTITY not in stdout
    for dangerous in (
        "\x00",
        "\x1b",
        "\r",
        "\t",
        "\u202a",
        "\u202e",
        "\u2066",
        "\u2069",
        "\u2603",
        "\u96ea",
    ):
        assert dangerous not in stdout
    assert_blind_output(
        stdout,
        raw_id,
        base64.b64encode(ADVERSARIAL_IDENTITY.encode("utf-8")).decode("ascii"),
        ADVERSARIAL_IDENTITY.encode("utf-8").hex(),
    )


def test_ascii_and_no_motion_flags_are_accepted_output_identical_and_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs: list[tuple[int, str, str]] = []
    for ascii_mode, no_motion in (
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ):
        install_catalog_source(
            monkeypatch,
            TrialCatalog(trials=(valid_inspection("raw-hidden"),)),
        )
        outputs.append(
            run_direct(
                watch_args(ascii=ascii_mode, no_motion=no_motion),
            )
        )

    assert outputs == [outputs[0]] * 4
    assert outputs[0][0] == 0
    assert outputs[0][2] == ""
    assert outputs[0][1].isascii()
    assert_blind_output(outputs[0][1])


class GuardedInput:
    def _forbidden(self, *args: object, **kwargs: object) -> Any:
        raise AssertionError("one-shot watch attempted input or TTY access")

    read = _forbidden
    readline = _forbidden
    fileno = _forbidden
    isatty = _forbidden


class GuardedOutput(io.StringIO):
    def read(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("one-shot watch attempted to read its output stream")

    def fileno(self) -> int:
        raise AssertionError("one-shot watch attempted output fileno access")

    def isatty(self) -> bool:
        raise AssertionError("one-shot watch attempted output TTY access")


class BinaryCapture:
    def __init__(
        self,
        *,
        fail_write: bool = False,
        fail_flush: bool = False,
    ) -> None:
        self.fail_write = fail_write
        self.fail_flush = fail_flush
        self.flush_calls = 0
        self._value = bytearray()

    def write(self, value: bytes | bytearray) -> int:
        if self.fail_write:
            raise OSError(f"write failed {BLIND_LEAK_SENTINEL}")
        encoded = bytes(value)
        self._value.extend(encoded)
        return len(encoded)

    def flush(self) -> None:
        self.flush_calls += 1
        if self.fail_flush:
            raise OSError(f"flush failed {BLIND_LEAK_SENTINEL}")

    def getvalue(self) -> bytes:
        return bytes(self._value)


class ShortWriteBinaryCapture(BinaryCapture):
    """A raw stream that accepts only a bounded prefix of each write."""

    def __init__(self, max_write: int = 3) -> None:
        super().__init__()
        self.max_write = max_write
        self.write_calls = 0

    def write(self, value: bytes | bytearray | memoryview) -> int:
        self.write_calls += 1
        return super().write(bytes(value)[: self.max_write])


class NewlineTranslatingDefaultStream:
    """A Windows-like text wrapper whose text path changes LF to CRLF."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, buffer: BinaryCapture | None = None) -> None:
        self.buffer = BinaryCapture() if buffer is None else buffer
        self.text_writes: list[str] = []

    def write(self, value: str) -> int:
        self.text_writes.append(value)
        translated = value.replace("\n", "\r\n").encode("utf-8")
        self.buffer.write(translated)
        return len(value)

    def flush(self) -> None:
        self.buffer.flush()

    def isatty(self) -> bool:
        raise AssertionError("one-shot watch attempted default-stream TTY access")

    def fileno(self) -> int:
        raise AssertionError("one-shot watch attempted default-stream fileno access")


class FailingTextStream(io.StringIO):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure

    def write(self, value: str) -> int:
        if self.failure == "write":
            raise OSError(f"text write failed {BLIND_LEAK_SENTINEL}")
        return super().write(value)

    def flush(self) -> None:
        if self.failure == "flush":
            raise OSError(f"text flush failed {BLIND_LEAK_SENTINEL}")
        super().flush()


def test_one_shot_watch_invokes_no_terminal_runtime_provider_tool_auth_or_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_id = f"raw-{BLIND_LEAK_SENTINEL}"
    event = TrialEvent.create(
        trial_id=raw_id,
        sequence=1,
        event_type="trial.started",
        payload={"arms": []},
        timestamp=1.0,
    )
    persist_events(tmp_path, "jsonl", (event,))
    before = tree_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("one-shot watch invoked an out-of-scope capability")

    monkeypatch.setattr(sys, "stdin", GuardedInput())
    monkeypatch.setattr(sys, "__stdin__", GuardedInput())
    monkeypatch.setattr(builtins, "input", forbidden)
    monkeypatch.setattr(os, "isatty", forbidden)
    monkeypatch.setattr(os, "get_terminal_size", forbidden)
    monkeypatch.setattr(shutil, "get_terminal_size", forbidden)
    monkeypatch.setattr(time, "sleep", forbidden)
    monkeypatch.setattr(threading, "Timer", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "check_call", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(webbrowser, "open", forbidden)
    monkeypatch.setattr(runtime.Runtime, "run", forbidden)
    monkeypatch.setattr(handlers, "create_model_provider", forbidden)
    monkeypatch.setattr(handlers, "create_default_model_provider", forbidden)
    monkeypatch.setattr(handlers, "default_record_store", forbidden)
    monkeypatch.setattr(handlers, "JsonlRecordStore", forbidden)
    monkeypatch.setattr(sqlite_store_module, "SqliteRecordStore", forbidden)
    monkeypatch.setattr(handlers.LocalToolRunner, "execute", forbidden)
    monkeypatch.setattr(tools.SandboxToolRunner, "execute", forbidden)
    monkeypatch.setattr(auth.TokenStore, "load_all", forbidden)
    monkeypatch.setattr(auth.TokenStore, "save_credential", forbidden)
    monkeypatch.setattr(auth, "login_google_antigravity", forbidden)
    monkeypatch.setattr(auth, "login_openai_codex", forbidden)
    monkeypatch.setattr(auth, "login_xai_grok", forbidden)
    monkeypatch.setattr(auth, "login_anthropic", forbidden)
    for name in (
        "sleep",
        "poll",
        "isatty",
        "fileno",
        "get_terminal_size",
        "setraw",
        "setcbreak",
    ):
        if hasattr(watch_cli, name):
            monkeypatch.setattr(watch_cli, name, forbidden)

    stdout = GuardedOutput()
    stderr = GuardedOutput()
    code, rendered, errors = run_direct(
        watch_args(raw_id, ascii=True, no_motion=True),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert errors == ""
    assert "selected: Trial 1" in rendered
    assert tree_snapshot(tmp_path) == before
    assert_blind_output(rendered, raw_id, str(tmp_path))


def invoke_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["arity", *arguments])
    code = cli_main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def invoke_cli_with_default_streams(
    monkeypatch: pytest.MonkeyPatch,
    *arguments: str,
    stdout_buffer: BinaryCapture | None = None,
    stderr_buffer: BinaryCapture | None = None,
) -> tuple[int, NewlineTranslatingDefaultStream, NewlineTranslatingDefaultStream]:
    stdout = NewlineTranslatingDefaultStream(stdout_buffer)
    stderr = NewlineTranslatingDefaultStream(stderr_buffer)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "argv", ["arity", *arguments])
    code = cli_main()
    return code, stdout, stderr


@pytest.mark.parametrize(
    ("arguments", "expected_code", "expected_stdout", "expected_stderr"),
    [
        (
            ("watch", "--ascii", "--no-motion"),
            0,
            b"No persisted trials.\n",
            b"",
        ),
        (
            ("watch", ADVERSARIAL_IDENTITY, "--ascii", "--no-motion"),
            3,
            b"",
            b"arity: trial_not_found\n",
        ),
    ],
)
def test_default_windows_like_streams_receive_exact_lf_ascii_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
    expected_code: int,
    expected_stdout: bytes,
    expected_stderr: bytes,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")

    code, stdout, stderr = invoke_cli_with_default_streams(
        monkeypatch,
        *arguments,
    )

    assert code == expected_code
    assert stdout.buffer.getvalue() == expected_stdout
    assert stderr.buffer.getvalue() == expected_stderr
    assert stdout.text_writes == []
    assert stderr.text_writes == []
    assert b"\r" not in expected_stdout + expected_stderr
    emitted = stdout.buffer if expected_stdout else stderr.buffer
    assert emitted.flush_calls >= 1
    assert not (tmp_path / ".gorkbot").exists()


def test_default_raw_output_completes_short_writes_without_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    short_stdout = ShortWriteBinaryCapture(max_write=3)

    code, stdout, stderr = invoke_cli_with_default_streams(
        monkeypatch,
        "watch",
        "--ascii",
        "--no-motion",
        stdout_buffer=short_stdout,
    )

    assert code == 0
    assert short_stdout.getvalue() == b"No persisted trials.\n"
    assert short_stdout.write_calls > 1
    assert short_stdout.flush_calls >= 1
    assert stdout.text_writes == []
    assert stderr.buffer.getvalue() == b""
    assert stderr.text_writes == []
    assert not (tmp_path / ".gorkbot").exists()


def test_real_closed_stdout_pipe_exits_one_without_shutdown_traceback(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["ARITY_STORE"] = "jsonl"
    environment["PYTHONPATH"] = str(repository)
    child = (
        "import sys; "
        "from arity.cli import main; "
        "sys.stdin.buffer.read(1); "
        "sys.argv=['arity','watch','--ascii','--no-motion']; "
        "raise SystemExit(main())"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        assert process.stdout is not None
        assert process.stdin is not None
        process.stdout.close()
        process.stdin.write(b"x")
        process.stdin.close()
        return_code = process.wait(timeout=15)
        assert process.stderr is not None
        errors = process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert return_code == 1
    assert errors == b""
    assert not (tmp_path / ".gorkbot").exists()


def test_explicit_text_streams_bypass_newline_translating_process_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_catalog_source(monkeypatch, TrialCatalog(trials=()))
    default_stdout = NewlineTranslatingDefaultStream()
    default_stderr = NewlineTranslatingDefaultStream()
    monkeypatch.setattr(sys, "stdout", default_stdout)
    monkeypatch.setattr(sys, "stderr", default_stderr)

    assert run_direct(watch_args(ascii=True, no_motion=True)) == (
        0,
        "No persisted trials.\n",
        "",
    )
    assert default_stdout.buffer.getvalue() == b""
    assert default_stderr.buffer.getvalue() == b""
    assert default_stdout.text_writes == []
    assert default_stderr.text_writes == []


@pytest.mark.parametrize(
    ("target", "failure"),
    [
        ("stdout", "write"),
        ("stdout", "flush"),
        ("stderr", "write"),
        ("stderr", "flush"),
    ],
)
def test_default_output_write_and_flush_failures_return_operational_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    failure: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    stdout_buffer = BinaryCapture(
        fail_write=target == "stdout" and failure == "write",
        fail_flush=target == "stdout" and failure == "flush",
    )
    stderr_buffer = BinaryCapture(
        fail_write=target == "stderr" and failure == "write",
        fail_flush=target == "stderr" and failure == "flush",
    )
    arguments = (
        ("watch", "--ascii", "--no-motion")
        if target == "stdout"
        else ("watch", ADVERSARIAL_IDENTITY, "--ascii", "--no-motion")
    )

    code, stdout, stderr = invoke_cli_with_default_streams(
        monkeypatch,
        *arguments,
        stdout_buffer=stdout_buffer,
        stderr_buffer=stderr_buffer,
    )

    assert code == 1
    combined = stdout.buffer.getvalue() + stderr.buffer.getvalue()
    assert b"Traceback" not in combined
    assert BLIND_LEAK_SENTINEL.encode("ascii") not in combined
    assert b"\x1b" not in combined
    assert b"\r" not in combined
    assert stdout.text_writes == []
    assert stderr.text_writes == []
    assert not (tmp_path / ".gorkbot").exists()


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_explicit_text_output_failures_also_return_operational_one(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    install_catalog_source(monkeypatch, TrialCatalog(trials=()))
    stdout = FailingTextStream(failure)
    stderr = io.StringIO()

    code = run_watch_command(
        watch_args(),
        clock=lambda: READ_AT,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 1
    assert "Traceback" not in stderr.getvalue()
    assert BLIND_LEAK_SENTINEL not in stderr.getvalue()


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_cli_main_real_one_shot_smoke_uses_default_handler_without_creating_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", backend)

    result = invoke_cli(
        monkeypatch,
        capsys,
        "watch",
        "--ascii",
        "--no-motion",
    )

    assert result == (0, "No persisted trials.\n", "")
    assert not (tmp_path / ".gorkbot").exists()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("watch",), (None, False, False)),
        (("watch", "raw-id"), ("raw-id", False, False)),
        (("watch", "raw-id", "--ascii", "--no-motion"), ("raw-id", True, True)),
        (("watch", "--no-motion", "--ascii", "raw-id"), ("raw-id", True, True)),
    ],
)
def test_cli_parser_dispatches_watch_once_with_exact_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
    expected: tuple[str | None, bool, bool],
) -> None:
    seen: list[tuple[str | None, bool, bool]] = []

    def handler(args: argparse.Namespace) -> int:
        seen.append((args.trial_id, args.ascii, args.no_motion))
        return 37

    monkeypatch.setattr(watch_cli, "run_watch_command", handler)

    code, stdout, stderr = invoke_cli(monkeypatch, capsys, *arguments)

    assert (code, stdout, stderr) == (37, "", "")
    assert seen == [expected]


@pytest.mark.parametrize(
    "arguments",
    [
        ("watch", ""),
        ("watch", "--json"),
        ("watch", "raw-id", "--json"),
        ("watch", "--ascii=yes"),
    ],
)
def test_cli_parser_errors_remain_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
) -> None:
    calls = 0

    def forbidden_handler(args: argparse.Namespace) -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(watch_cli, "run_watch_command", forbidden_handler)
    monkeypatch.setattr(sys, "argv", ["arity", *arguments])

    with pytest.raises(SystemExit) as captured:
        cli_main()

    output = capsys.readouterr()
    assert captured.value.code == 2
    assert output.out == ""
    assert output.err
    assert calls == 0


def test_watch_registration_does_not_capture_existing_inspection_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[tuple[str, str | None, bool]] = []

    def trials_handler(args: argparse.Namespace) -> int:
        seen.append((args.command, None, args.json))
        return 31

    def trial_handler(args: argparse.Namespace) -> int:
        seen.append((args.command, args.trial_action, args.json))
        return 32

    def watch_forbidden(args: argparse.Namespace) -> int:
        raise AssertionError("existing inspection command dispatched to watch")

    monkeypatch.setattr(inspection_cli, "run_trials_command", trials_handler)
    monkeypatch.setattr(inspection_cli, "run_trial_command", trial_handler)
    monkeypatch.setattr(watch_cli, "run_watch_command", watch_forbidden)

    assert invoke_cli(monkeypatch, capsys, "trials", "--json") == (31, "", "")
    assert invoke_cli(
        monkeypatch, capsys, "trial", "show", "raw-id", "--json",
    ) == (32, "", "")
    assert invoke_cli(
        monkeypatch, capsys, "trial", "replay", "raw-id",
    ) == (32, "", "")
    assert seen == [
        ("trials", None, True),
        ("trial", "show", True),
        ("trial", "replay", False),
    ]
