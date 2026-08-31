"""Integration contracts for blind-safe cache deadlines in the live watch view."""
from __future__ import annotations

import argparse
import dataclasses
import io

import pytest

import arity.watch_terminal as watch_terminal
import arity.watch_view_model as watch_view_model
from arity.cache_heat import CacheHeatView
from arity.inspection import TrialCatalog, TrialInspection, inspect_trial
from arity.telemetry import CachePolicyHint, TokenMeasurement, UsageEvidence
from arity.trial_events import TrialEvent, replay_trial
from arity.watch_follow import FollowController
from arity.watch_cli import run_watch_command
from arity.watch_terminal import (
    TerminalCapabilities,
    render_watch_follow_frame,
    render_watch_snapshot,
)
from arity.watch_view_model import WatchProjector, watch_fingerprint


PRIVATE_TRIAL = "PRIVATE_CACHE_TRIAL_DO_NOT_RENDER"
PRIVATE_ARM = "PRIVATE_CACHE_ARM_DO_NOT_RENDER"
PRIVATE_PROVIDER = "PRIVATE_PROVIDER_DO_NOT_RENDER"


def _usage_payload(*, started_at: float, window: int) -> dict[str, object]:
    evidence = UsageEvidence(
        input_tokens=TokenMeasurement(100, "provider_reported"),
        output_tokens=TokenMeasurement(10, "provider_reported"),
        cache_read_tokens=TokenMeasurement(80, "provider_reported"),
        cache_write_tokens=TokenMeasurement(None, "unavailable"),
        evidence_observed_at=started_at + 2.0,
        cache_policy=CachePolicyHint(
            window_seconds=window,
            refresh_on_reuse=True,
            basis="configured",
            clock_basis="request_started",
        ),
    )
    return {
        "phase": "trial",
        "arm_id": PRIVATE_ARM,
        "actor_kind": "candidate",
        "actor_ref": PRIVATE_ARM,
        "request_ordinal": 1,
        "outcome": "completed",
        "request_started_at": started_at,
        "evidence": evidence.to_dict(),
    }


def _catalog(*, started_at: float = 100.0, window: int = 300) -> TrialCatalog:
    started = TrialEvent.create(
        trial_id=PRIVATE_TRIAL,
        sequence=1,
        event_type="trial.started",
        payload={
            "arms": [{"arm_id": PRIVATE_ARM, "arm_ordinal": 0}],
            "provider": PRIVATE_PROVIDER,
        },
        timestamp=1.0,
    )
    usage = TrialEvent.create(
        trial_id=PRIVATE_TRIAL,
        sequence=2,
        event_type="request.usage_recorded",
        payload=_usage_payload(started_at=started_at, window=window),
        timestamp=started_at + 2.0,
    )
    replay = replay_trial((started, usage), trial_id=PRIVATE_TRIAL)
    return TrialCatalog(
        trials=(
            TrialInspection(
                trial_id=PRIVATE_TRIAL,
                integrity="valid",
                status="started",
                events=(),
                replay=replay,
            ),
        )
    )


def _project(*, mode: str, now: float, started_at: float = 100.0):
    return WatchProjector(cache_policy=mode).project(
        _catalog(started_at=started_at),
        backend="jsonl",
        read_at=now,
        selected_trial_id=PRIVATE_TRIAL,
    )


def test_projector_adds_only_bounded_cache_heat_and_off_omits_it() -> None:
    exact = _project(mode="exact", now=110.0)
    off = _project(mode="off", now=110.0)

    assert exact.trials[0].detail is not None
    assert exact.trials[0].detail.cache_heat == CacheHeatView(
        state="confirmed",
        deadline_at=400.0,
        seconds_remaining=290,
    )
    assert off.trials[0].detail is not None
    assert off.trials[0].detail.cache_heat is None
    for projected in (exact, off):
        encoded = repr(dataclasses.asdict(projected))
        assert PRIVATE_TRIAL not in encoded
        assert PRIVATE_ARM not in encoded
        assert PRIVATE_PROVIDER not in encoded


def test_cache_clock_aging_never_becomes_a_journal_update_fingerprint() -> None:
    warm = _project(mode="exact", now=110.0)
    elapsed = _project(mode="exact", now=500.0)
    refreshed = _project(mode="exact", now=210.0, started_at=200.0)

    assert warm.trials[0].detail is not None
    assert elapsed.trials[0].detail is not None
    assert warm.trials[0].detail.cache_heat is not None
    assert elapsed.trials[0].detail.cache_heat is not None
    assert warm.trials[0].detail.cache_heat.state == "confirmed"
    assert elapsed.trials[0].detail.cache_heat.state == "elapsed"
    assert watch_fingerprint(warm) == watch_fingerprint(elapsed)
    assert watch_fingerprint(warm) != watch_fingerprint(refreshed)


def test_follow_expansion_shows_stable_deadline_but_one_shot_stays_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _project(mode="exact", now=110.0)
    monkeypatch.setattr(watch_terminal, "_default_read_time", lambda value: "12:34:56")

    expanded = render_watch_follow_frame(
        model,
        TerminalCapabilities(width=100, ascii=True, motion=False, color=False),
        expanded=True,
    )
    collapsed = render_watch_follow_frame(
        model,
        TerminalCapabilities(width=100, ascii=True, motion=False, color=False),
        expanded=False,
    )
    one_shot = render_watch_snapshot(model)

    assert (
        "cache deadline | respond by 12:34:56 | prior activity confirmed | "
        "eligibility only"
        in expanded
    )
    assert "cache deadline" not in collapsed
    assert "cache deadline" not in one_shot
    for rendered in (expanded, collapsed, one_shot):
        assert PRIVATE_TRIAL not in rendered
        assert PRIVATE_ARM not in rendered
        assert PRIVATE_PROVIDER not in rendered


def test_projector_rejects_unrecognized_cache_policy_without_echoing_it() -> None:
    with pytest.raises(ValueError) as stopped:
        WatchProjector(cache_policy=PRIVATE_PROVIDER)
    assert PRIVATE_PROVIDER not in str(stopped.value)


def test_forged_usage_projection_without_a_matching_event_fails_closed() -> None:
    source = _catalog().trials[0]
    assert source.replay is not None
    forged_replay = dataclasses.replace(
        source.replay,
        request_usage=(_usage_payload(started_at=200.0, window=300),),
    )
    forged = TrialCatalog(
        trials=(dataclasses.replace(source, replay=forged_replay),)
    )

    model = WatchProjector(cache_policy="exact").project(
        forged,
        backend="jsonl",
        read_at=210.0,
    )

    assert model.catalog_integrity == "corrupt"
    assert model.trials[0].detail is None
    assert model.trials[0].issue is not None
    assert model.trials[0].issue.code == "inspection_incomplete"


class _EventReader:
    def __init__(self, events: tuple[TrialEvent, ...]) -> None:
        self.events = events

    def query(self, kind: str, **filters):
        assert kind == "trial_event"
        assert filters == {"trial_id": PRIVATE_TRIAL}
        return tuple(event.to_dict() for event in self.events)

    def close(self) -> None:
        return None


def test_forged_usage_time_fails_closed_before_cache_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = TrialEvent.create(
        trial_id=PRIVATE_TRIAL,
        sequence=1,
        event_type="trial.started",
        payload={"arms": [{"arm_id": PRIVATE_ARM, "arm_ordinal": 0}]},
        timestamp=1.0,
    )
    usage = TrialEvent.create(
        trial_id=PRIVATE_TRIAL,
        sequence=2,
        event_type="request.usage_recorded",
        payload=_usage_payload(started_at=100.0, window=300),
        timestamp=999.0,
    )
    inspection = inspect_trial(_EventReader((started, usage)), PRIVATE_TRIAL)

    assert inspection.integrity == "corrupt"
    assert inspection.replay is None
    assert inspection.issues[0].code == "invalid_replay"

    def forbidden_cache_projection(*args, **kwargs):
        del args, kwargs
        raise AssertionError("corrupt replay reached cache projection")

    monkeypatch.setattr(
        watch_view_model,
        "project_cache_heat",
        forbidden_cache_projection,
    )
    model = WatchProjector(cache_policy="exact").project(
        TrialCatalog(trials=(inspection,)),
        backend="jsonl",
        read_at=110.0,
    )

    assert model.catalog_integrity == "corrupt"
    assert model.trials[0].detail is None
    assert model.trials[0].issue is not None
    assert model.trials[0].issue.code == "invalid_replay"


class _UnavailableTerminal:
    def stdin_isatty(self) -> bool:
        return False


def test_off_policy_survives_noninteractive_one_shot_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projected = []

    def forbidden_cache_projection(*args, **kwargs):
        del args, kwargs
        raise AssertionError("off policy inspected cache evidence")

    monkeypatch.setattr(
        watch_view_model,
        "project_cache_heat",
        forbidden_cache_projection,
    )

    def loader(
        store_spec=None,
        *,
        selected_trial_id=None,
        clock=None,
        projector=None,
    ):
        del store_spec, clock
        assert type(projector) is WatchProjector
        model = projector.project(
            _catalog(),
            backend="jsonl",
            read_at=110.0,
            selected_trial_id=selected_trial_id,
        )
        projected.append(model)
        return model

    code = run_watch_command(
        argparse.Namespace(
            trial_id=PRIVATE_TRIAL,
            follow=True,
            ascii=True,
            no_motion=True,
            cache_policy="off",
        ),
        terminal=_UnavailableTerminal(),
        model_loader=loader,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    assert len(projected) == 1
    assert projected[0].trials[0].detail is not None
    assert projected[0].trials[0].detail.cache_heat is None


class _FollowTerminal:
    capabilities = TerminalCapabilities(
        width=100,
        ascii=True,
        motion=False,
        color=False,
    )

    def __init__(self) -> None:
        self.keys: list[str | None] = ["enter", None, "q"]
        self.frames: list[str] = []

    def draw(self, frame: str) -> None:
        self.frames.append(frame)

    def read_key(self, timeout: float) -> str | None:
        assert timeout > 0
        return self.keys.pop(0)


def test_follow_frame_does_not_tick_or_reclassify_without_a_journal_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projector = WatchProjector(cache_policy="exact")
    terminal = _FollowTerminal()
    read_times = iter((110.0, 500.0))

    def loader(selected_trial_id: str | None):
        return projector.project(
            _catalog(),
            backend="jsonl",
            read_at=next(read_times),
            selected_trial_id=selected_trial_id,
        )

    monkeypatch.setattr(watch_terminal, "_default_read_time", lambda value: "12:34:56")
    controller = FollowController(
        terminal=terminal,
        loader=loader,
        projector=projector,
        monotonic=lambda: 0.0,
        refresh_interval=1.0,
    )

    assert controller.run(PRIVATE_TRIAL) == 0
    assert len(terminal.frames) == 3
    assert terminal.frames[1] == terminal.frames[2]
    assert "journal update" not in terminal.frames[2]
