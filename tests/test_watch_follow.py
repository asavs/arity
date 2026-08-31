"""Adversarial contracts for the explicit, read-only ``arity watch --follow`` loop.

The controller is exercised only through injected terminal, clock, and model-loader
fakes.  No test in this module needs a real console, store, provider, or credential.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, cast
from unittest.mock import patch

import pytest

import arity.watch_cli as watch_cli
from arity.cli import main as cli_main
from arity.inspection import TrialCatalog, TrialInspection
from arity.record_readers import RecordChanged, RecordCorruption, RecordReadError
from arity.trial_events import TrialEvent, TrialReplay
from arity.watch_cli import run_watch_command
from arity.watch_terminal import render_watch_snapshot
from arity.watch_view_model import (
    BoundedCount,
    WatchAgent,
    WatchProjector,
    WatchTrial,
    WatchTrialDetail,
    WatchViewModel,
)


LEAK = "FOLLOW_RAW_ID_LEAK_SENTINEL"
HOSTILE_ID = LEAK + "\x00\x1b[31m\r\n\t\u202a\u202e\u2066\u2069\u2603"
TIMEOUT = object()
EOF_INPUT = object()
INTERRUPT = object()
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_BIDI = {"\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"}


def watch_args(
    trial_id: str | None = None,
    *,
    follow: bool = True,
    ascii: bool = False,
    no_motion: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        trial_id=trial_id,
        follow=follow,
        ascii=ascii,
        no_motion=no_motion,
    )


def bounded(value: int) -> BoundedCount:
    return BoundedCount(value=value)


def detail(completions: tuple[bool, ...] = (False,)) -> WatchTrialDetail:
    return WatchTrialDetail(
        agents=tuple(
            WatchAgent(position=index, completion_recorded=complete)
            for index, complete in enumerate(completions)
        ),
        arms=bounded(len(completions)),
        completed_agents=bounded(sum(completions)),
        evidence=bounded(0),
        reviews=bounded(0),
        resolutions=bounded(0),
        delivery_recorded=False,
    )


def safe_model(
    *lifecycles: str,
    selected_number: int | None = None,
    read_at: float = 100.0,
) -> WatchViewModel:
    values = lifecycles or ("started",)
    trials = tuple(
        WatchTrial(
            trial_number=index,
            integrity="valid",
            lifecycle=cast(Any, lifecycle),
            detail=detail(),
            issue=None,
            selected=index == selected_number,
        )
        for index, lifecycle in enumerate(values, 1)
    )
    return WatchViewModel(
        backend="jsonl",
        read_at=read_at,
        trials=trials,
        more_trials_omitted=False,
        catalog_integrity="valid",
        selected_trial_number=selected_number,
    )


def started_inspection(
    raw_id: str,
    *,
    timestamp: float,
    completions: tuple[Mapping[str, Any], ...] = (),
) -> TrialInspection:
    started = TrialEvent.create(
        trial_id=raw_id,
        sequence=1,
        event_type="trial.started",
        payload={"arms": [{"arm_id": f"arm:{raw_id}", "arm_ordinal": 0}]},
        timestamp=timestamp,
        idempotency_key=f"key:{raw_id}",
    )
    replay = TrialReplay(
        trial_id=raw_id,
        events=(started,),
        started=started.payload,
        completed_arms=completions,
        evidence_bundles=(),
        reviews=(),
        evaluations=(),
        resolutions=(),
        resolution_sequences=(),
        delivery=None,
        unhandled_events=(),
    )
    return TrialInspection(
        trial_id=raw_id,
        integrity="valid",
        status="started",
        events=(),
        replay=replay,
    )


class FakeClock:
    def __init__(self, *values: float) -> None:
        self.values = values or (100.0,)
        self.calls = 0

    def __call__(self) -> float:
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


class SequenceLoader:
    """A query-only model loader that never owns a writable store."""

    def __init__(self, *snapshots: TrialCatalog | WatchViewModel | BaseException) -> None:
        self.snapshots = snapshots
        self.calls = 0
        self.projectors: list[WatchProjector | None] = []
        self.selected_ids: list[str | None] = []

    def __call__(
        self,
        store_spec: object = None,
        *,
        selected_trial_id: str | None = None,
        clock=None,
        projector: WatchProjector | None = None,
        **kwargs: object,
    ) -> WatchViewModel:
        del store_spec, kwargs
        self.projectors.append(projector)
        self.selected_ids.append(selected_trial_id)
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        snapshot = self.snapshots[index]
        if isinstance(snapshot, BaseException):
            raise snapshot
        if isinstance(snapshot, WatchViewModel):
            return snapshot
        if projector is None:
            projector = WatchProjector()
        read_at = 100.0 if clock is None else clock()
        return projector.project(
            snapshot,
            backend="jsonl",
            read_at=read_at,
            selected_trial_id=selected_trial_id,
        )


class FakeTerminal:
    """Controller-facing terminal protocol with observable, idempotent cleanup."""

    def __init__(
        self,
        *keys: object,
        stdin_tty: bool = True,
        stdout_tty: bool = True,
        supported: bool = True,
        width: object = 80,
        fail_setup_after: str | None = None,
        fail_draw_at: int | None = None,
    ) -> None:
        self.keys = list(keys)
        self.stdin_tty = stdin_tty
        self.stdout_tty = stdout_tty
        self.supported = supported
        self.reported_width = width
        self.fail_setup_after = fail_setup_after
        self.fail_draw_at = fail_draw_at
        self.actions: list[str] = []
        self.frames: list[str] = []
        self.alt_screen = False
        self.cursor_hidden = False
        self.raw_mode = False
        self.restore_calls = 0

    def stdin_isatty(self) -> bool:
        self.actions.append("stdin_isatty")
        return self.stdin_tty

    def stdout_isatty(self) -> bool:
        self.actions.append("stdout_isatty")
        return self.stdout_tty

    def supports_interactive(self) -> bool:
        self.actions.append("supports_interactive")
        return self.supported

    def width(self) -> object:
        self.actions.append("width")
        return self.reported_width

    def setup(self) -> None:
        self.actions.append("enter_alt_screen")
        self.alt_screen = True
        if self.fail_setup_after == "alt":
            raise OSError(f"partial setup {LEAK}")
        self.actions.append("hide_cursor")
        self.cursor_hidden = True
        if self.fail_setup_after == "cursor":
            raise OSError(f"partial setup {LEAK}")
        self.actions.append("enable_raw_mode")
        self.raw_mode = True
        if self.fail_setup_after == "raw":
            raise OSError(f"partial setup {LEAK}")

    def restore(self) -> None:
        self.restore_calls += 1
        if self.raw_mode:
            self.actions.append("disable_raw_mode")
            self.raw_mode = False
        if self.cursor_hidden:
            self.actions.append("show_cursor")
            self.cursor_hidden = False
        if self.alt_screen:
            self.actions.append("leave_alt_screen")
            self.alt_screen = False

    def draw(self, frame: str) -> None:
        self.actions.append("draw")
        if self.fail_draw_at == len(self.frames):
            raise OSError(f"render failed {LEAK}")
        self.frames.append(frame)

    def read_key(self, timeout: float) -> str | None:
        assert type(timeout) in {float, int} and timeout > 0
        self.actions.append("read_key")
        if not self.keys:
            raise EOFError
        value = self.keys.pop(0)
        if value is TIMEOUT:
            return None
        if value is EOF_INPUT:
            raise EOFError
        if value is INTERRUPT:
            raise KeyboardInterrupt
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, str)
        return value


class ForbiddenTerminal:
    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"ordinary watch touched terminal method {name}")


def run_injected(
    args: argparse.Namespace,
    *,
    loader: SequenceLoader,
    terminal: object,
    clock: FakeClock | None = None,
    renderer=None,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_watch_command(
        args,
        clock=clock or FakeClock(),
        stdout=stdout,
        stderr=stderr,
        renderer=renderer,
        terminal=terminal,
        model_loader=loader,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def assert_terminal_restored(terminal: FakeTerminal) -> None:
    assert terminal.restore_calls == 1
    assert not terminal.alt_screen
    assert not terminal.cursor_hidden
    assert not terminal.raw_mode


def assert_no_raw_identity(value: str, *raw_ids: str) -> None:
    for raw_id in (LEAK, HOSTILE_ID, *raw_ids):
        assert raw_id not in value
    for character in _BIDI:
        assert character not in value
    assert "\x00" not in value
    assert "\r" not in value
    without_sgr = _SGR.sub("", value)
    assert "\x1b" not in without_sgr


def test_cli_requires_explicit_follow_and_preserves_ordinary_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[tuple[bool, bool, bool]] = []

    def handler(args: argparse.Namespace) -> int:
        seen.append((args.follow, args.ascii, args.no_motion))
        return 23

    monkeypatch.setattr(watch_cli, "run_watch_command", handler)
    for arguments in (("watch",), ("watch", "--follow")):
        monkeypatch.setattr(sys, "argv", ["arity", *arguments])
        assert cli_main() == 23
        assert capsys.readouterr() == ("", "")

    assert seen == [(False, False, False), (True, False, False)]


def test_ordinary_watch_never_instantiates_or_inspects_terminal() -> None:
    snapshot = safe_model("started", read_at=100.0)
    loader = SequenceLoader(snapshot)

    code, stdout, stderr = run_injected(
        watch_args(follow=False),
        loader=loader,
        terminal=ForbiddenTerminal(),
    )

    assert (code, stdout, stderr) == (0, render_watch_snapshot(snapshot), "")
    assert loader.calls == 1


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty", "supported"),
    [
        (False, False, True),
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_explicit_follow_capability_failure_is_one_shot_fallback(
    stdin_tty: bool,
    stdout_tty: bool,
    supported: bool,
) -> None:
    snapshot = safe_model("started", read_at=100.0)
    loader = SequenceLoader(snapshot)
    terminal = FakeTerminal(
        "q",
        stdin_tty=stdin_tty,
        stdout_tty=stdout_tty,
        supported=supported,
    )

    code, stdout, stderr = run_injected(
        watch_args(), loader=loader, terminal=terminal,
    )

    assert (code, stdout, stderr) == (0, render_watch_snapshot(snapshot), "")
    assert loader.calls == 1
    assert terminal.frames == []
    assert "enter_alt_screen" not in terminal.actions
    assert "read_key" not in terminal.actions
    assert terminal.restore_calls == 0


def test_follow_reuses_one_projector_and_never_recycles_private_labels() -> None:
    raw_a = f"a:{HOSTILE_ID}"
    raw_b = f"b:{HOSTILE_ID}"
    raw_c = f"c:{HOSTILE_ID}"
    raw_d = f"d:{HOSTILE_ID}"
    snapshots = (
        TrialCatalog(trials=(
            started_inspection(raw_a, timestamp=30.0),
            started_inspection(raw_b, timestamp=20.0),
        )),
        TrialCatalog(trials=(
            started_inspection(raw_b, timestamp=50.0),
            started_inspection(raw_c, timestamp=40.0),
            started_inspection(raw_a, timestamp=30.0),
        )),
        TrialCatalog(trials=(
            started_inspection(raw_c, timestamp=60.0),
            started_inspection(raw_a, timestamp=30.0),
        )),
        TrialCatalog(trials=(
            started_inspection(raw_d, timestamp=70.0),
            started_inspection(raw_c, timestamp=60.0),
            started_inspection(raw_a, timestamp=30.0),
        )),
        TrialCatalog(trials=(
            started_inspection(raw_b, timestamp=80.0),
            started_inspection(raw_d, timestamp=70.0),
            started_inspection(raw_c, timestamp=60.0),
            started_inspection(raw_a, timestamp=30.0),
        )),
    )
    loader = SequenceLoader(*snapshots)
    terminal = FakeTerminal(TIMEOUT, TIMEOUT, TIMEOUT, TIMEOUT, "q")

    code, stdout, stderr = run_injected(
        watch_args(raw_b),
        loader=loader,
        terminal=terminal,
        clock=FakeClock(100.0, 101.0, 102.0, 103.0, 104.0),
    )

    assert (code, stdout, stderr) == (0, "", "")
    assert loader.calls == 5
    assert len({id(projector) for projector in loader.projectors}) == 1
    projector = loader.projectors[0]
    assert projector is not None
    assert projector._trial_id_for_number(1) == raw_a
    assert projector._trial_id_for_number(2) == raw_b
    assert projector._trial_id_for_number(3) == raw_c
    assert projector._trial_id_for_number(4) == raw_d
    assert all(raw_id not in repr(projector) for raw_id in (raw_a, raw_b, raw_c, raw_d))

    assert len(terminal.frames) == 5
    assert "> Trial 2" in terminal.frames[0]
    assert all(label in terminal.frames[1] for label in ("Trial 1", "Trial 2", "Trial 3"))
    assert "Trial 2" not in terminal.frames[2]
    assert all(label in terminal.frames[3] for label in ("Trial 1", "Trial 3", "Trial 4"))
    assert "> Trial 2" in terminal.frames[4]
    joined = "".join(terminal.frames) + stdout + stderr
    assert_no_raw_identity(joined, raw_a, raw_b, raw_c, raw_d)
    assert_terminal_restored(terminal)
