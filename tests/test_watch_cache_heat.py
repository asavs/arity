"""Integration contracts for blind-safe cache deadlines in the live watch view."""
from __future__ import annotations

import dataclasses

import pytest

import arity.watch_terminal as watch_terminal
from arity.cache_heat import CacheHeatView
from arity.inspection import TrialCatalog, TrialInspection
from arity.telemetry import CachePolicyHint, TokenMeasurement, UsageEvidence
from arity.trial_events import TrialEvent, replay_trial
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
        timestamp=2.0,
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

    assert "cache window confirmed | respond by 12:34:56 | eligibility only" in expanded
    assert "cache window" not in collapsed
    assert "cache window" not in one_shot
    for rendered in (expanded, collapsed, one_shot):
        assert PRIVATE_TRIAL not in rendered
        assert PRIVATE_ARM not in rendered
        assert PRIVATE_PROVIDER not in rendered


def test_projector_rejects_unrecognized_cache_policy_without_echoing_it() -> None:
    with pytest.raises(ValueError) as stopped:
        WatchProjector(cache_policy=PRIVATE_PROVIDER)
    assert PRIVATE_PROVIDER not in str(stopped.value)
