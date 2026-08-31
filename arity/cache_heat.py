"""Pure, blind-safe cache-heat projection from normalized usage evidence.

The projection intentionally knows nothing about providers, models, seats, prompts,
or context strategies.  A deadline is a display aid derived from an explicitly
recorded cache policy; it is never a claim that an entry still resides in a cache.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Any, Literal, Optional

from .telemetry import UnsupportedUsageEvidenceSchema, UsageEvidence


CacheHeatMode = Literal["conservative", "exact", "off"]
CacheHeatState = Literal["confirmed", "estimated", "elapsed", "unknown", "unsupported"]
CacheActivityConfidence = Literal["confirmed", "estimated"]

_MODES = frozenset({"conservative", "exact", "off"})
_STATES = frozenset({"confirmed", "estimated", "elapsed", "unknown", "unsupported"})
_NO_TIMING_STATES = frozenset({"unknown", "unsupported"})
_POSITIVE_CACHE_BASES = frozenset({"provider_reported", "estimated", "synthetic"})

# A corrupt caller must not be able to make a read-only observer consume an
# infinite iterator or retain an unbounded trial history.
MAX_CACHE_USAGE_RECORDS = 4096


@dataclass(frozen=True)
class CacheHeatView:
    """Small immutable display value containing no persisted identity or text.

    ``deadline_at`` is stable while the journal is unchanged.  The presentation
    countdown is deliberately excluded from equality and hashing so a follow UI
    can avoid timer-only update pulses or redraws.
    """

    state: CacheHeatState
    activity_confidence: Optional[CacheActivityConfidence] = None
    deadline_at: Optional[float] = None
    seconds_remaining: Optional[int] = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if type(self.state) is not str or self.state not in _STATES:
            raise ValueError("cache heat state is unsupported")
        if self.activity_confidence is None and self.state in {
            "confirmed",
            "estimated",
        }:
            object.__setattr__(self, "activity_confidence", self.state)
        if self.activity_confidence is not None and (
            type(self.activity_confidence) is not str
            or self.activity_confidence not in {"confirmed", "estimated"}
        ):
            raise ValueError("cache activity confidence is unsupported")
        if self.deadline_at is not None:
            if isinstance(self.deadline_at, bool) or not isinstance(
                self.deadline_at, (int, float)
            ):
                raise TypeError("cache heat deadline must be a number or null")
            resolved_deadline = float(self.deadline_at)
            if not math.isfinite(resolved_deadline):
                raise ValueError("cache heat deadline must be finite")
            object.__setattr__(self, "deadline_at", resolved_deadline)
        if self.seconds_remaining is not None and (
            type(self.seconds_remaining) is not int or self.seconds_remaining < 0
        ):
            if type(self.seconds_remaining) is not int:
                raise TypeError("cache heat seconds must be a non-negative integer or null")
            raise ValueError("cache heat seconds must not be negative")
        if self.state in _NO_TIMING_STATES:
            if (
                self.activity_confidence is not None
                or self.deadline_at is not None
                or self.seconds_remaining is not None
            ):
                raise ValueError("non-timing cache heat states cannot expose timing")
        elif self.deadline_at is None or self.seconds_remaining is None:
            raise ValueError("timed cache heat states require a deadline and countdown")
        elif self.activity_confidence is None:
            raise ValueError("timed cache heat states require activity confidence")
        elif self.state in {"confirmed", "estimated"} and (
            self.activity_confidence != self.state
        ):
            raise ValueError("cache heat state and activity confidence disagree")
        if self.state == "elapsed" and self.seconds_remaining != 0:
            raise ValueError("elapsed cache heat must have zero seconds remaining")

    @property
    def stable_fingerprint(self) -> tuple[str, Optional[float]]:
        """Return a journal-stable key that excludes clock-only presentation state."""

        if self.deadline_at is not None:
            assert self.activity_confidence is not None
            return (self.activity_confidence, self.deadline_at)
        return (self.state, None)

    def to_dict(self) -> dict[str, Any]:
        """Return the complete, bounded public projection."""

        return {
            "state": self.state,
            "activity_confidence": self.activity_confidence,
            "deadline_at": self.deadline_at,
            "seconds_remaining": self.seconds_remaining,
        }


@dataclass(frozen=True, repr=False)
class _Record:
    evidence: UsageEvidence
    request_started_at: Optional[float]
    group: str


@dataclass(frozen=True, repr=False)
class _Candidate:
    group: str
    anchor: float
    window_seconds: int
    confidence: CacheHeatState


def _finite_number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    resolved = float(value)
    return resolved if math.isfinite(resolved) else None


def _source_items(source: object) -> Iterable[object]:
    if isinstance(source, (UsageEvidence, Mapping)):
        return (source,)
    if isinstance(source, (str, bytes, bytearray)):
        return ()
    try:
        return iter(source)  # type: ignore[arg-type]
    except TypeError:
        return ()


def _decode_evidence(value: object) -> tuple[Optional[UsageEvidence], bool]:
    if type(value) is UsageEvidence:
        return value, False
    if not isinstance(value, Mapping):
        return None, False
    try:
        return UsageEvidence.from_dict(value), False
    except UnsupportedUsageEvidenceSchema:
        return None, True
    except (KeyError, TypeError, ValueError):
        return None, False


def _records(
    source: object,
    *,
    arm_id: Optional[str],
) -> tuple[list[_Record], bool]:
    records: list[_Record] = []
    unsupported = False
    for item in islice(_source_items(source), MAX_CACHE_USAGE_RECORDS):
        if type(item) is UsageEvidence:
            if arm_id is not None:
                continue
            records.append(
                _Record(
                    evidence=item,
                    request_started_at=None,
                    group="direct",
                )
            )
            continue
        if not isinstance(item, Mapping):
            continue

        if "evidence" in item:
            raw_arm_id = item.get("arm_id")
            if arm_id is not None and raw_arm_id != arm_id:
                continue
            # Failed requests are normalized as unknown evidence and cannot
            # establish cache activity.  Ignore them even if an untrusted caller
            # hands the projector a mapping that bypassed replay validation.
            if item.get("outcome", "completed") != "completed":
                continue
            evidence, future_schema = _decode_evidence(item.get("evidence"))
            unsupported = unsupported or future_schema
            if evidence is None:
                continue
            started_at = _finite_number(item.get("request_started_at"))
            group = raw_arm_id if type(raw_arm_id) is str else "anonymous"
            records.append(
                _Record(
                    evidence=evidence,
                    request_started_at=started_at,
                    group=group,
                )
            )
            continue

        # A normalized UsageEvidence mapping may be projected directly when the
        # caller has already selected its trial/arm.
        if arm_id is not None:
            continue
        evidence, future_schema = _decode_evidence(item)
        unsupported = unsupported or future_schema
        if evidence is not None:
            records.append(
                _Record(
                    evidence=evidence,
                    request_started_at=None,
                    group="direct",
                )
            )
    return records, unsupported


def _clock_anchor(record: _Record) -> Optional[float]:
    evidence = record.evidence
    clock_basis = evidence.cache_policy.clock_basis
    if clock_basis == "request_started":
        return record.request_started_at
    if clock_basis == "response_received":
        return evidence.evidence_observed_at
    if clock_basis == "provider_timestamp":
        return evidence.provider_timestamp
    return None


def _cache_confidence(evidence: UsageEvidence) -> CacheHeatState:
    positive = [
        measurement
        for measurement in (evidence.cache_read_tokens, evidence.cache_write_tokens)
        if measurement.value is not None
        and measurement.value > 0
        and measurement.basis in _POSITIVE_CACHE_BASES
    ]
    if any(measurement.basis == "provider_reported" for measurement in positive):
        return "confirmed"
    return "estimated"


def _can_start_deadline(evidence: UsageEvidence) -> bool:
    write = evidence.cache_write_tokens
    if (
        write.value is not None
        and write.value > 0
        and write.basis in _POSITIVE_CACHE_BASES
    ):
        return True
    # With a refreshing recorded policy, a successful request is sufficient for
    # an explicitly estimated eligibility deadline even when the provider omits
    # cache counters.  Context shape is intentionally not consulted.  A positive
    # provider-reported counter upgrades confidence separately.
    return evidence.cache_policy.refresh_on_reuse is True


def _known_window(record: _Record, *, now: float) -> tuple[Optional[int], Optional[float]]:
    policy = record.evidence.cache_policy
    if policy.basis not in {"configured", "provider_reported"}:
        return None, None
    if policy.window_seconds is None:
        return None, None
    anchor = _clock_anchor(record)
    if anchor is None or not math.isfinite(anchor) or anchor > now:
        return None, None
    return policy.window_seconds, anchor


def _latest_by_group(candidates: list[_Candidate]) -> list[_Candidate]:
    grouped: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.group, []).append(candidate)

    latest: list[_Candidate] = []
    for values in grouped.values():
        latest_anchor = max(candidate.anchor for candidate in values)
        at_anchor = [candidate for candidate in values if candidate.anchor == latest_anchor]
        shortest_window = min(candidate.window_seconds for candidate in at_anchor)
        confidence: CacheHeatState = (
            "confirmed"
            if any(candidate.confidence == "confirmed" for candidate in at_anchor)
            else "estimated"
        )
        latest.append(
            _Candidate(
                group=at_anchor[0].group,
                anchor=latest_anchor,
                window_seconds=shortest_window,
                confidence=confidence,
            )
        )
    return latest


def project_cache_heat(
    request_usage: object,
    *,
    now: float,
    mode: CacheHeatMode = "conservative",
    arm_id: Optional[str] = None,
) -> CacheHeatView:
    """Project normalized request usage into a tiny cache-deadline view.

    ``request_usage`` may be ``TrialReplay.request_usage``, one normalized replay
    payload, or a ``UsageEvidence`` value.  In ``conservative`` mode the shortest
    usable recorded policy window is applied throughout the selected scope and the
    earliest current arm deadline wins.  ``exact`` uses each activity's own
    recorded policy.  ``off`` returns a constant non-timing view without inspecting
    evidence, so even policy shape cannot become an identity fingerprint.
    """

    if type(mode) is not str or mode not in _MODES:
        raise ValueError("cache heat mode is unsupported")
    if mode == "off":
        return CacheHeatView(state="unknown")
    if arm_id is not None and type(arm_id) is not str:
        raise TypeError("cache heat arm selector must be a string or null")
    resolved_now = _finite_number(now)
    if resolved_now is None:
        return CacheHeatView(state="unknown")

    records, unsupported = _records(request_usage, arm_id=arm_id)
    if unsupported:
        return CacheHeatView(state="unsupported")

    known: list[tuple[_Record, int, float]] = []
    for record in records:
        window, anchor = _known_window(record, now=resolved_now)
        if window is not None and anchor is not None:
            known.append((record, window, anchor))
    if not known:
        return CacheHeatView(state="unknown")

    conservative_window = min(window for _record, window, _anchor in known)
    candidates: list[_Candidate] = []
    for record, exact_window, anchor in known:
        confidence = _cache_confidence(record.evidence)
        if not _can_start_deadline(record.evidence):
            continue
        window = conservative_window if mode == "conservative" else exact_window
        deadline = anchor + window
        if not math.isfinite(deadline):
            continue
        candidates.append(
            _Candidate(
                group=record.group,
                anchor=anchor,
                window_seconds=window,
                confidence=confidence,
            )
        )
    if not candidates:
        return CacheHeatView(state="unknown")

    current = _latest_by_group(candidates)
    deadlines = [candidate.anchor + candidate.window_seconds for candidate in current]
    deadline_at = min(deadlines)
    confidence: CacheActivityConfidence = (
        "confirmed"
        if all(candidate.confidence == "confirmed" for candidate in current)
        else "estimated"
    )
    if resolved_now >= deadline_at:
        return CacheHeatView(
            state="elapsed",
            activity_confidence=confidence,
            deadline_at=deadline_at,
            seconds_remaining=0,
        )

    return CacheHeatView(
        state=confidence,
        activity_confidence=confidence,
        deadline_at=deadline_at,
        seconds_remaining=max(0, math.ceil(deadline_at - resolved_now)),
    )


__all__ = [
    "CacheActivityConfidence",
    "CacheHeatMode",
    "CacheHeatState",
    "CacheHeatView",
    "MAX_CACHE_USAGE_RECORDS",
    "project_cache_heat",
]
