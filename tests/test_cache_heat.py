"""Contracts for the blind-safe cache-heat projection."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from arity.cache_heat import CacheHeatView, project_cache_heat
from arity.telemetry import CachePolicyHint, TokenMeasurement, UsageEvidence


def _measurement(value: int | None, basis: str = "provider_reported") -> TokenMeasurement:
    if value is None:
        return TokenMeasurement(None, "unavailable")
    return TokenMeasurement(value, basis)


def _evidence(
    *,
    observed_at: float = 102.0,
    read: int | None = 20,
    write: int | None = None,
    cache_basis: str = "provider_reported",
    window: int | None = 300,
    policy_basis: str = "configured",
    clock_basis: str = "request_started",
    refresh_on_reuse: bool | None = True,
    provider_timestamp: float | None = None,
) -> UsageEvidence:
    policy = (
        CachePolicyHint()
        if window is None
        else CachePolicyHint(
            window_seconds=window,
            refresh_on_reuse=refresh_on_reuse,
            basis=policy_basis,
            clock_basis=clock_basis,
        )
    )
    return UsageEvidence(
        input_tokens=TokenMeasurement(100, "provider_reported"),
        output_tokens=TokenMeasurement(10, "provider_reported"),
        cache_read_tokens=_measurement(read, cache_basis),
        cache_write_tokens=_measurement(write, cache_basis),
        evidence_observed_at=observed_at,
        provider_timestamp=provider_timestamp,
        provider_timestamp_basis="response" if provider_timestamp is not None else "unknown",
        cache_policy=policy,
    )


def _record(
    evidence: UsageEvidence | dict[str, object],
    *,
    arm_id: str = "arm-a",
    started_at: float = 100.0,
    ordinal: int = 1,
    **extra: object,
) -> dict[str, object]:
    return {
        "phase": "trial",
        "arm_id": arm_id,
        "actor_kind": "candidate",
        "actor_ref": arm_id,
        "request_ordinal": ordinal,
        "outcome": "completed",
        "request_started_at": started_at,
        "evidence": evidence.to_dict() if isinstance(evidence, UsageEvidence) else evidence,
        **extra,
    }


def test_cache_heat_view_is_immutable_bounded_and_has_a_stable_fingerprint() -> None:
    first = project_cache_heat([_record(_evidence())], now=110.0, mode="exact")
    later = project_cache_heat([_record(_evidence())], now=120.0, mode="exact")
    elapsed = project_cache_heat([_record(_evidence())], now=401.0, mode="exact")

    assert first == CacheHeatView(
        state="confirmed",
        deadline_at=400.0,
        seconds_remaining=290,
    )
    assert later.seconds_remaining == 280
    assert first == later
    assert first.stable_fingerprint == later.stable_fingerprint
    assert elapsed.state == "elapsed"
    assert first.stable_fingerprint == elapsed.stable_fingerprint
    assert first.to_dict() == {
        "state": "confirmed",
        "deadline_at": 400.0,
        "seconds_remaining": 290,
    }
    with pytest.raises(FrozenInstanceError):
        first.state = "unknown"  # type: ignore[misc]


@pytest.mark.parametrize("mode", ["exact", "conservative"])
@pytest.mark.parametrize("field", ["read", "write"])
def test_only_positive_provider_reported_cache_activity_confirms(
    mode: str, field: str,
) -> None:
    values = {"read": None, "write": None, field: 1}
    view = project_cache_heat(
        [_record(_evidence(read=values["read"], write=values["write"]))],
        now=110.0,
        mode=mode,
    )

    assert view.state == "confirmed"
    assert view.deadline_at == 400.0


@pytest.mark.parametrize("basis", ["estimated", "synthetic"])
def test_non_provider_cache_counts_are_estimated_and_never_confirm(basis: str) -> None:
    view = project_cache_heat(
        [_record(_evidence(cache_basis=basis))],
        now=110.0,
        mode="exact",
    )

    assert view.state == "estimated"
    assert view.deadline_at == 400.0


@pytest.mark.parametrize("cache_count", [None, 0])
def test_context_or_ordinary_tokens_never_invent_confirmation(
    cache_count: int | None,
) -> None:
    marker = "PRIVATE_FORK_CONTEXT"
    view = project_cache_heat(
        [
            _record(
                _evidence(read=cache_count, write=cache_count),
                context="fork",
                private_context=marker,
            )
        ],
        now=110.0,
        mode="exact",
    )

    assert view == CacheHeatView(
        state="estimated",
        deadline_at=400.0,
        seconds_remaining=290,
    )
    assert marker not in repr(view)
    assert marker not in repr(view.to_dict())


def test_elapsed_reports_only_that_the_recorded_window_passed() -> None:
    view = project_cache_heat([_record(_evidence(window=5))], now=105.0, mode="exact")

    assert view == CacheHeatView(
        state="elapsed",
        deadline_at=105.0,
        seconds_remaining=0,
    )
    assert set(view.to_dict()) == {"state", "deadline_at", "seconds_remaining"}


def test_unknown_policy_or_unusable_clock_yields_no_timing_claim() -> None:
    unknown_policy = project_cache_heat(
        [_record(_evidence(window=None))], now=110.0, mode="exact"
    )
    missing_provider_clock = project_cache_heat(
        [
            _record(
                _evidence(
                    clock_basis="provider_timestamp",
                    provider_timestamp=None,
                )
            )
        ],
        now=110.0,
        mode="exact",
    )

    assert unknown_policy == CacheHeatView(state="unknown")
    assert missing_provider_clock == CacheHeatView(state="unknown")


def test_response_and_provider_clock_bases_use_only_their_recorded_anchor() -> None:
    response = project_cache_heat(
        [
            _record(
                _evidence(observed_at=120.0, window=10, clock_basis="response_received"),
                started_at=1.0,
            )
        ],
        now=125.0,
        mode="exact",
    )
    provider = project_cache_heat(
        [
            _record(
                _evidence(
                    observed_at=999.0,
                    window=10,
                    clock_basis="provider_timestamp",
                    provider_timestamp=130.0,
                ),
                started_at=1.0,
            )
        ],
        now=135.0,
        mode="exact",
    )

    assert response.deadline_at == 130.0
    assert provider.deadline_at == 140.0


def test_read_without_refresh_or_write_cannot_start_a_new_deadline() -> None:
    read_only = project_cache_heat(
        [_record(_evidence(refresh_on_reuse=False))],
        now=110.0,
        mode="exact",
    )
    write = project_cache_heat(
        [
            _record(
                _evidence(read=None, write=10, refresh_on_reuse=False),
            )
        ],
        now=110.0,
        mode="exact",
    )

    assert read_only == CacheHeatView(state="unknown")
    assert write.state == "confirmed"


def test_conservative_uses_shortest_recorded_window_and_never_extends_exact() -> None:
    records = [
        _record(_evidence(window=3600), ordinal=1, started_at=100.0),
        _record(
            _evidence(
                read=None,
                write=None,
                window=300,
                refresh_on_reuse=False,
            ),
            ordinal=2,
            started_at=101.0,
        ),
    ]

    exact = project_cache_heat(records, now=110.0, mode="exact", arm_id="arm-a")
    conservative = project_cache_heat(
        records, now=110.0, mode="conservative", arm_id="arm-a"
    )

    assert exact.deadline_at == 3700.0
    assert conservative.deadline_at == 400.0
    assert conservative.deadline_at <= exact.deadline_at


def test_conservative_trial_view_uses_earliest_current_arm_deadline() -> None:
    records = [
        _record(_evidence(window=300), arm_id="arm-a", started_at=100.0),
        _record(_evidence(window=300), arm_id="arm-b", started_at=105.0),
    ]

    whole_trial = project_cache_heat(records, now=110.0, mode="conservative")
    selected_arm = project_cache_heat(
        records, now=110.0, mode="conservative", arm_id="arm-b"
    )

    assert whole_trial.deadline_at == 400.0
    assert selected_arm.deadline_at == 405.0


def test_latest_refresh_replaces_an_older_deadline_without_order_dependence() -> None:
    earlier = _record(_evidence(window=300), started_at=100.0, ordinal=1)
    later = _record(_evidence(window=300), started_at=200.0, ordinal=2)

    forward = project_cache_heat([earlier, later], now=210.0, mode="exact")
    reverse = project_cache_heat([later, earlier], now=210.0, mode="exact")

    assert forward.deadline_at == 500.0
    assert forward == reverse


def test_future_nested_usage_is_unsupported_without_exposing_raw_fields() -> None:
    marker = "PRIVATE_FUTURE_CACHE_POLICY"
    future = _evidence().to_dict()
    future["schema_version"] = 99
    future["private_future_field"] = marker

    view = project_cache_heat([_record(future)], now=110.0, mode="exact")

    assert view == CacheHeatView(state="unsupported")
    assert marker not in repr(view)
    assert marker not in repr(view.to_dict())


def test_off_is_constant_and_does_not_reveal_future_schema_or_timing() -> None:
    marker = "PRIVATE_OFF_MODE_MARKER"
    future = {"schema_version": 99, "private_future_field": marker}

    view = project_cache_heat(
        [_record(future, arm_id=marker, started_at=123456.0)],
        now=float("nan"),
        mode="off",
        arm_id=marker,
    )

    assert view == CacheHeatView(state="unknown")
    assert view.deadline_at is None
    assert view.seconds_remaining is None
    assert marker not in repr(view)
    assert marker not in repr(view.to_dict())


@pytest.mark.parametrize("now", [float("nan"), float("inf"), float("-inf"), True])
def test_nonfinite_or_lossy_projection_clock_is_blind_safe_unknown(now: object) -> None:
    view = project_cache_heat([_record(_evidence())], now=now, mode="exact")  # type: ignore[arg-type]

    assert view == CacheHeatView(state="unknown")


def test_future_or_malformed_evidence_for_an_unselected_arm_is_ignored() -> None:
    future = _evidence().to_dict()
    future["schema_version"] = 2
    selected = _record(_evidence(), arm_id="selected")
    other = _record(future, arm_id="other")

    view = project_cache_heat(
        [other, selected], now=110.0, mode="exact", arm_id="selected"
    )

    assert view.state == "confirmed"


def test_direct_usage_evidence_is_supported_without_replay_identity() -> None:
    evidence = _evidence(
        observed_at=100.0,
        window=30,
        clock_basis="response_received",
    )

    view = project_cache_heat(evidence, now=110.0, mode="exact")

    assert view == CacheHeatView(
        state="confirmed",
        deadline_at=130.0,
        seconds_remaining=20,
    )


def test_malformed_current_data_and_future_anchors_make_no_claim() -> None:
    malformed = _record({"schema_version": 1, "private": "DO_NOT_ECHO"})
    future_anchor = _record(_evidence(), started_at=120.0)

    assert project_cache_heat([malformed], now=110.0, mode="exact") == CacheHeatView(
        state="unknown"
    )
    assert project_cache_heat([future_anchor], now=110.0, mode="exact") == CacheHeatView(
        state="unknown"
    )


def test_invalid_mode_is_rejected_without_echoing_the_value() -> None:
    marker = "PRIVATE_INVALID_MODE"
    with pytest.raises(ValueError) as stopped:
        project_cache_heat([], now=0.0, mode=marker)  # type: ignore[arg-type]
    assert marker not in str(stopped.value)
