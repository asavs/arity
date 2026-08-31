"""Strict provider-agnostic usage evidence and durable recording wrappers."""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional

from .seams import ModelProvider
from .types import CallModel, ModelCompleted, ModelFailed


USAGE_EVIDENCE_SCHEMA_VERSION = 1
TOKEN_BASES = frozenset({"provider_reported", "estimated", "synthetic", "unavailable"})
CACHE_POLICY_BASES = frozenset({"provider_reported", "configured", "unknown"})
CACHE_CLOCK_BASES = frozenset(
    {"request_started", "response_received", "provider_timestamp", "unknown"}
)
PROVIDER_TIMESTAMP_BASES = frozenset({"request", "response", "unknown"})
_MISSING = object()


class UnsupportedUsageEvidenceSchema(ValueError):
    """A persisted usage value uses a newer nested contract."""

    document_type = "usage evidence"

    def __init__(self, schema_version: int) -> None:
        super().__init__(f"unsupported usage evidence schema version {schema_version}")
        self.schema_version = schema_version


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    resolved = float(value)
    if not math.isfinite(resolved):
        raise ValueError(f"{label} must be finite")
    return resolved


def _exact_keys(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} fields differ: expected {sorted(expected)!r}, got {sorted(actual)!r}"
        )


@dataclass(frozen=True)
class TokenMeasurement:
    """One logical token count with an explicit evidence basis."""

    value: Optional[int]
    basis: str

    def __post_init__(self) -> None:
        if type(self.basis) is not str or self.basis not in TOKEN_BASES:
            raise ValueError("token measurement has an unsupported basis")
        if self.value is not None and (type(self.value) is not int or self.value < 0):
            if type(self.value) is not int:
                raise TypeError("token measurement value must be a non-negative integer or null")
            raise ValueError("token measurement value must not be negative")
        if self.basis == "unavailable" and self.value is not None:
            raise ValueError("unavailable token measurement must have a null value")
        if self.basis != "unavailable" and self.value is None:
            raise ValueError("known token measurement basis requires a value")

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "basis": self.basis}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenMeasurement":
        if not isinstance(value, Mapping):
            raise TypeError("token measurement must be a JSON object")
        _exact_keys(value, {"value", "basis"}, label="token measurement")
        basis = value["basis"]
        if not isinstance(basis, str):
            raise TypeError("token measurement basis must be a string")
        return cls(value=value["value"], basis=basis)


def _unknown_measurement() -> TokenMeasurement:
    return TokenMeasurement(None, "unavailable")


@dataclass(frozen=True)
class CachePolicyHint:
    """A documented/configured reuse window, never a residency assertion."""

    window_seconds: Optional[int] = None
    refresh_on_reuse: Optional[bool] = None
    basis: str = "unknown"
    clock_basis: str = "unknown"

    def __post_init__(self) -> None:
        if type(self.basis) is not str or self.basis not in CACHE_POLICY_BASES:
            raise ValueError("cache policy has an unsupported basis")
        if type(self.clock_basis) is not str or self.clock_basis not in CACHE_CLOCK_BASES:
            raise ValueError("cache policy has an unsupported clock basis")
        if self.window_seconds is not None and (
            type(self.window_seconds) is not int or self.window_seconds <= 0
        ):
            if type(self.window_seconds) is not int:
                raise TypeError("cache window must be a positive integer or null")
            raise ValueError("cache window must be positive")
        if self.refresh_on_reuse is not None and type(self.refresh_on_reuse) is not bool:
            raise TypeError("cache refresh flag must be a boolean or null")
        if self.basis == "unknown":
            if (
                self.window_seconds is not None
                or self.refresh_on_reuse is not None
                or self.clock_basis != "unknown"
            ):
                raise ValueError("unknown cache policy cannot claim timing semantics")
        elif self.window_seconds is None or self.clock_basis == "unknown":
            raise ValueError("known cache policy requires a window and clock basis")

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_seconds": self.window_seconds,
            "refresh_on_reuse": self.refresh_on_reuse,
            "basis": self.basis,
            "clock_basis": self.clock_basis,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CachePolicyHint":
        if not isinstance(value, Mapping):
            raise TypeError("cache policy must be a JSON object")
        _exact_keys(
            value,
            {"window_seconds", "refresh_on_reuse", "basis", "clock_basis"},
            label="cache policy",
        )
        return cls(
            window_seconds=value["window_seconds"],
            refresh_on_reuse=value["refresh_on_reuse"],
            basis=value["basis"],
            clock_basis=value["clock_basis"],
        )


@dataclass(frozen=True)
class UsageEvidence:
    """Versioned measurements from one logical model request."""

    input_tokens: TokenMeasurement = field(default_factory=_unknown_measurement)
    output_tokens: TokenMeasurement = field(default_factory=_unknown_measurement)
    cache_read_tokens: TokenMeasurement = field(default_factory=_unknown_measurement)
    cache_write_tokens: TokenMeasurement = field(default_factory=_unknown_measurement)
    evidence_observed_at: float = 0.0
    provider_timestamp: Optional[float] = None
    provider_timestamp_basis: str = "unknown"
    cache_policy: CachePolicyHint = field(default_factory=CachePolicyHint)

    def __post_init__(self) -> None:
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        ):
            if type(value) is not TokenMeasurement:
                raise TypeError("usage measurements must be exact TokenMeasurement values")
        object.__setattr__(
            self,
            "evidence_observed_at",
            _finite_number(self.evidence_observed_at, label="evidence observed time"),
        )
        if self.provider_timestamp is None:
            if self.provider_timestamp_basis != "unknown":
                raise ValueError("missing provider timestamp requires an unknown basis")
        else:
            object.__setattr__(
                self,
                "provider_timestamp",
                _finite_number(self.provider_timestamp, label="provider timestamp"),
            )
            if self.provider_timestamp_basis == "unknown":
                raise ValueError("provider timestamp requires an explicit basis")
        if (
            type(self.provider_timestamp_basis) is not str
            or self.provider_timestamp_basis not in PROVIDER_TIMESTAMP_BASES
        ):
            raise ValueError("usage evidence has an unsupported provider timestamp basis")
        if type(self.cache_policy) is not CachePolicyHint:
            raise TypeError("usage evidence cache policy must be an exact CachePolicyHint")

    @classmethod
    def unknown(
        cls,
        *,
        evidence_observed_at: float,
        cache_policy: Optional[CachePolicyHint] = None,
    ) -> "UsageEvidence":
        return cls(
            evidence_observed_at=evidence_observed_at,
            cache_policy=cache_policy if cache_policy is not None else CachePolicyHint(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": USAGE_EVIDENCE_SCHEMA_VERSION,
            "measurements": {
                "input_tokens": self.input_tokens.to_dict(),
                "output_tokens": self.output_tokens.to_dict(),
                "cache_read_tokens": self.cache_read_tokens.to_dict(),
                "cache_write_tokens": self.cache_write_tokens.to_dict(),
            },
            "evidence_observed_at": self.evidence_observed_at,
            "provider_timestamp": self.provider_timestamp,
            "provider_timestamp_basis": self.provider_timestamp_basis,
            "cache_policy": self.cache_policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UsageEvidence":
        if not isinstance(value, Mapping):
            raise TypeError("usage evidence must be a JSON object")
        schema_version = value.get("schema_version")
        if type(schema_version) is not int:
            raise TypeError("usage evidence schema_version must be an integer")
        if schema_version != USAGE_EVIDENCE_SCHEMA_VERSION:
            raise UnsupportedUsageEvidenceSchema(schema_version)
        _exact_keys(
            value,
            {
                "schema_version",
                "measurements",
                "evidence_observed_at",
                "provider_timestamp",
                "provider_timestamp_basis",
                "cache_policy",
            },
            label="usage evidence",
        )
        measurements = value["measurements"]
        if not isinstance(measurements, Mapping):
            raise TypeError("usage measurements must be a JSON object")
        expected_measurements = {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        }
        _exact_keys(measurements, expected_measurements, label="usage measurements")
        timestamp_basis = value["provider_timestamp_basis"]
        if not isinstance(timestamp_basis, str):
            raise TypeError("provider timestamp basis must be a string")
        return cls(
            input_tokens=TokenMeasurement.from_dict(measurements["input_tokens"]),
            output_tokens=TokenMeasurement.from_dict(measurements["output_tokens"]),
            cache_read_tokens=TokenMeasurement.from_dict(measurements["cache_read_tokens"]),
            cache_write_tokens=TokenMeasurement.from_dict(measurements["cache_write_tokens"]),
            evidence_observed_at=value["evidence_observed_at"],
            provider_timestamp=value["provider_timestamp"],
            provider_timestamp_basis=timestamp_basis,
            cache_policy=CachePolicyHint.from_dict(value["cache_policy"]),
        )


def _nested(value: Mapping[str, Any], *path: str) -> object:
    current: object = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _first(value: Mapping[str, Any], *paths: tuple[str, ...]) -> object:
    for path in paths:
        found = _nested(value, *path)
        if found is not _MISSING:
            return found
    return _MISSING


def _measurement(raw: object, basis: str) -> TokenMeasurement:
    if raw is _MISSING or raw is None:
        return _unknown_measurement()
    return TokenMeasurement(raw, basis)  # type: ignore[arg-type]


def normalize_usage_evidence(
    usage: Mapping[str, Any],
    *,
    evidence_observed_at: float,
    cache_policy: Optional[CachePolicyHint] = None,
) -> UsageEvidence:
    """Normalize current OpenAI-, Anthropic-, and Gemini-shaped token metadata."""

    if not isinstance(usage, Mapping):
        raise TypeError("model usage must be a JSON object")
    estimated = usage.get("estimated", False)
    if type(estimated) is not bool:
        raise TypeError("usage estimated flag must be a boolean")
    ordinary_basis = "estimated" if estimated else "provider_reported"
    input_value = _first(usage, ("input_tokens",), ("prompt_tokens",))
    output_value = _first(usage, ("output_tokens",), ("completion_tokens",))
    cache_read_value = _first(
        usage,
        ("cache_read_input_tokens",),
        ("cached_tokens",),
        ("input_tokens_details", "cached_tokens"),
        ("prompt_tokens_details", "cached_tokens"),
    )
    cache_write_value = _first(
        usage,
        ("cache_creation_input_tokens",),
        ("cache_write_input_tokens",),
    )
    return UsageEvidence(
        input_tokens=_measurement(input_value, ordinary_basis),
        output_tokens=_measurement(output_value, ordinary_basis),
        cache_read_tokens=_measurement(cache_read_value, ordinary_basis),
        cache_write_tokens=_measurement(cache_write_value, ordinary_basis),
        evidence_observed_at=evidence_observed_at,
        cache_policy=cache_policy if cache_policy is not None else CachePolicyHint(),
    )


class RequestOrdinalSource:
    """Thread-safe request numbering scoped to one actor and trial phase."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 1

    def take(self) -> int:
        with self._lock:
            value = self._next
            self._next += 1
            return value

    def __repr__(self) -> str:
        return "RequestOrdinalSource(<private counter>)"


@dataclass(frozen=True)
class UsageRecordingContext:
    """Immutable trial identity and policy attached to one provider wrapper."""

    journal: Any = field(repr=False, compare=False)
    phase: str = "trial"
    arm_id: str = ""
    actor_kind: str = "candidate"
    actor_ref: str = ""
    cache_policy: CachePolicyHint = field(default_factory=CachePolicyHint)
    ordinals: RequestOrdinalSource = field(
        default_factory=RequestOrdinalSource, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.phase not in {"trial", "conference"}:
            raise ValueError("usage recording phase is unsupported")
        if type(self.arm_id) is not str or not self.arm_id:
            raise ValueError("usage recording requires a non-empty arm id")
        if self.actor_kind != "candidate":
            raise ValueError("usage recording actor kind is unsupported")
        if type(self.actor_ref) is not str or not self.actor_ref:
            raise ValueError("usage recording requires a non-empty actor reference")
        if type(self.cache_policy) is not CachePolicyHint:
            raise TypeError("usage recording policy must be an exact CachePolicyHint")
        if type(self.ordinals) is not RequestOrdinalSource:
            raise TypeError("usage recording ordinals must be a RequestOrdinalSource")
        if not hasattr(self.journal, "append"):
            raise TypeError("usage recording journal must provide append")


Clock = Callable[[], float]


@dataclass
class JournaledModelProvider:
    """Persist normalized request evidence before publishing a provider result."""

    provider: ModelProvider
    context: UsageRecordingContext
    clock: Clock = time.time

    def __getattr__(self, name: str) -> Any:
        """Preserve non-call provider capabilities such as fallback accounting."""
        provider = object.__getattribute__(self, "provider")
        return getattr(provider, name)

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        started_at = _finite_number(self.clock(), label="request start time")
        ordinal = self.context.ordinals.take()
        result: ModelCompleted | ModelFailed
        try:
            result = self.provider.call(effect)
        except Exception:
            observed_at = _finite_number(self.clock(), label="evidence observed time")
            evidence = UsageEvidence.unknown(
                evidence_observed_at=observed_at,
                cache_policy=self.context.cache_policy,
            )
            self._append(ordinal, started_at, observed_at, "failed", evidence)
            raise

        observed_at = _finite_number(self.clock(), label="evidence observed time")
        if isinstance(result, ModelCompleted):
            if result.usage_evidence is None:
                evidence = normalize_usage_evidence(
                    result.usage,
                    evidence_observed_at=observed_at,
                    cache_policy=self.context.cache_policy,
                )
            else:
                evidence = replace(
                    result.usage_evidence,
                    evidence_observed_at=observed_at,
                )
            result = replace(result, usage_evidence=evidence)
            outcome = "completed"
        elif isinstance(result, ModelFailed):
            evidence = UsageEvidence.unknown(
                evidence_observed_at=observed_at,
                cache_policy=self.context.cache_policy,
            )
            outcome = "failed"
        else:
            raise TypeError("model provider returned an unsupported result")

        self._append(ordinal, started_at, observed_at, outcome, evidence)
        return result

    def _append(
        self,
        ordinal: int,
        started_at: float,
        observed_at: float,
        outcome: str,
        evidence: UsageEvidence,
    ) -> None:
        if started_at > observed_at:
            raise ValueError("request start time cannot be after evidence observation")
        if evidence.evidence_observed_at != observed_at:
            raise ValueError("usage evidence observed time must match journal event time")
        payload = {
            "phase": self.context.phase,
            "arm_id": self.context.arm_id,
            "actor_kind": self.context.actor_kind,
            "actor_ref": self.context.actor_ref,
            "request_ordinal": ordinal,
            "outcome": outcome,
            "request_started_at": started_at,
            "evidence": evidence.to_dict(),
        }
        key = (
            f"request.usage:{self.context.phase}:{self.context.actor_kind}:"
            f"{self.context.actor_ref}:{ordinal}"
        )
        self.context.journal.append(
            "request.usage_recorded",
            payload,
            timestamp=observed_at,
            idempotency_key=key,
        )


__all__ = [
    "CACHE_CLOCK_BASES",
    "CACHE_POLICY_BASES",
    "PROVIDER_TIMESTAMP_BASES",
    "TOKEN_BASES",
    "USAGE_EVIDENCE_SCHEMA_VERSION",
    "CachePolicyHint",
    "JournaledModelProvider",
    "RequestOrdinalSource",
    "TokenMeasurement",
    "UnsupportedUsageEvidenceSchema",
    "UsageEvidence",
    "UsageRecordingContext",
    "normalize_usage_evidence",
]
