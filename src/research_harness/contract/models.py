from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator
from pydantic_core import core_schema

_DURATION_PATTERN = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>s|m|h|d)$",
    re.IGNORECASE,
)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class Duration:
    """Human-readable duration such as ``90s``, ``20m``, or ``24h``."""

    __slots__ = ("seconds",)

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    @classmethod
    def parse(cls, value: str | float | int | Duration | dict[str, Any]) -> Duration:
        if isinstance(value, Duration):
            return value
        if isinstance(value, dict) and "seconds" in value:
            return cls(seconds=float(value["seconds"]))
        if isinstance(value, (int, float)):
            return cls(seconds=float(value))
        text = str(value).strip()
        match = _DURATION_PATTERN.match(text)
        if not match:
            msg = f"Invalid duration: {value!r}. Expected format like 90s, 20m, 24h."
            raise ValueError(msg)
        amount = float(match.group("value"))
        unit = match.group("unit").lower()
        return cls(seconds=amount * _UNIT_SECONDS[unit])

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Duration):
            return NotImplemented
        return self.seconds == other.seconds

    def __repr__(self) -> str:
        return f"Duration({self!s})"

    def __str__(self) -> str:
        if self.seconds % 86400 == 0:
            return f"{int(self.seconds // 86400)}d"
        if self.seconds % 3600 == 0:
            return f"{int(self.seconds // 3600)}h"
        if self.seconds % 60 == 0:
            return f"{int(self.seconds // 60)}m"
        return f"{int(self.seconds)}s"

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: Any,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls.parse,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: str(value),
                return_schema=core_schema.str_schema(),
            ),
        )


def parse_duration(value: Any) -> Duration:
    return Duration.parse(value)


DurationField = Annotated[Duration, Field(description="Duration like 90s, 20m, or 24h")]


class ProjectInfo(BaseModel):
    id: str = Field(min_length=1)
    objective: str = Field(min_length=1)


class InvariantViolationAction(StrEnum):
    HALT = "halt"
    BLOCK = "block"
    INCIDENT = "incident"


class Invariant(BaseModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    check: str = Field(min_length=1)
    on_violation: str = Field(default="halt")

    @field_validator("on_violation")
    @classmethod
    def validate_on_violation(cls, value: str) -> str:
        allowed = {"halt", "block", "incident"}
        if value not in allowed:
            msg = f"on_violation must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value


class RelaunchAction(StrEnum):
    NO_ACTION = "no_action"
    HOT_RELOAD = "hot_reload"
    WORKER_RESTART = "worker_restart"
    SERVICE_RESTART = "service_restart"
    CONTAINER_REBUILD = "rebuild"
    FULL_RELAUNCH = "full_relaunch"


class FingerprintConfig(BaseModel):
    fields: list[str] = Field(min_length=1)
    on_change: dict[str, str] = Field(default_factory=dict)
    default: str = "full_relaunch"

    @field_validator("on_change")
    @classmethod
    def validate_on_change_actions(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "no_action",
            "hot_reload",
            "worker_restart",
            "service_restart",
            "rebuild",
            "full_relaunch",
        }
        for field, action in value.items():
            if action not in allowed:
                msg = f"Unknown relaunch action {action!r} for field {field!r}"
                raise ValueError(msg)
        return value

    @field_validator("default")
    @classmethod
    def validate_default_action(cls, value: str) -> str:
        allowed = {
            "no_action",
            "hot_reload",
            "worker_restart",
            "service_restart",
            "rebuild",
            "full_relaunch",
        }
        if value not in allowed:
            msg = f"default must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value

    def action_for_field(self, field: str) -> str:
        return self.on_change.get(field, self.default)


class ProgressWatermark(BaseModel):
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    stall_after: DurationField

    @field_validator("stall_after", mode="before")
    @classmethod
    def parse_stall_after(cls, value: Any) -> Duration:
        return parse_duration(value)


class PhaseConfig(BaseModel):
    stall_after: DurationField

    @field_validator("stall_after", mode="before")
    @classmethod
    def parse_stall_after(cls, value: Any) -> Duration:
        return parse_duration(value)


class ProgressConfig(BaseModel):
    watermarks: list[ProgressWatermark] = Field(min_length=1)
    phases: dict[str, PhaseConfig] = Field(default_factory=dict)
    slow_operation_grace: DurationField | None = None
    stall_requires: str = "any"

    @field_validator("slow_operation_grace", mode="before")
    @classmethod
    def parse_slow_operation_grace(cls, value: Any) -> Duration | None:
        if value is None:
            return None
        return parse_duration(value)

    @field_validator("stall_requires")
    @classmethod
    def validate_stall_requires(cls, value: str) -> str:
        if value not in {"any", "all"}:
            msg = "stall_requires must be 'any' or 'all'"
            raise ValueError(msg)
        return value


class ValidityCheck(BaseModel):
    id: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    on_fail: str = "incident"

    @field_validator("on_fail")
    @classmethod
    def validate_on_fail(cls, value: str) -> str:
        allowed = {"incident", "block", "quarantine", "discard"}
        if value not in allowed:
            msg = f"on_fail must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value


class ValidityConfig(BaseModel):
    expected_units: int = Field(ge=0)
    max_null_rate: float = Field(ge=0.0, le=1.0)
    max_error_rate: float = Field(ge=0.0, le=1.0)
    require_fingerprint_match: bool = True
    checks: list[ValidityCheck] = Field(default_factory=list)
    on_invalid: str = "quarantine"

    @field_validator("on_invalid")
    @classmethod
    def validate_on_invalid(cls, value: str) -> str:
        allowed = {"quarantine", "discard", "incident"}
        if value not in allowed:
            msg = f"on_invalid must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value


class ScopedPathRule(BaseModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class AllowListRule(BaseModel):
    allow: list[str] = Field(default_factory=list)


class AuthorityConfig(BaseModel):
    code_changes: ScopedPathRule | bool = True
    dependency_changes: str = "none"
    model_swaps: AllowListRule | bool = False
    provider_swaps: bool = False
    runtime_restarts: bool = False
    architecture_changes: bool = False

    @field_validator("dependency_changes")
    @classmethod
    def validate_dependency_changes(cls, value: str) -> str:
        allowed = {"none", "patch_only", "minor", "any"}
        if value not in allowed:
            msg = f"dependency_changes must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value


class BudgetConfig(BaseModel):
    total_usd: float = Field(ge=0.0)
    per_hour_usd: float | None = Field(default=None, ge=0.0)
    per_incident_usd: float | None = Field(default=None, ge=0.0)
    per_incident_wallclock: DurationField | None = None
    warn_at: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("per_incident_wallclock", mode="before")
    @classmethod
    def parse_per_incident_wallclock(cls, value: Any) -> Duration | None:
        if value is None:
            return None
        return parse_duration(value)


class RecoveryConfig(BaseModel):
    max_identical_attempts: int = Field(default=2, ge=1)
    max_attempts_per_incident: int = Field(default=6, ge=1)
    detect_oscillation: bool = True
    oscillation_window: int = Field(default=4, ge=2)
    backoff: str = "exponential"
    min_backoff: DurationField = Field(default_factory=lambda: Duration(seconds=30))
    novel_strategy_requires: str = "evidence_delta"

    @field_validator("min_backoff", mode="before")
    @classmethod
    def parse_min_backoff(cls, value: Any) -> Duration:
        return parse_duration(value)

    @field_validator("backoff")
    @classmethod
    def validate_backoff(cls, value: str) -> str:
        allowed = {"none", "linear", "exponential"}
        if value not in allowed:
            msg = f"backoff must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value


class StableAfterConfig(BaseModel):
    units: int = Field(ge=1)
    min_duration: DurationField
    no_recurrence_within: DurationField | None = None

    @field_validator("min_duration", "no_recurrence_within", mode="before")
    @classmethod
    def parse_durations(cls, value: Any) -> Duration | None:
        if value is None:
            return None
        return parse_duration(value)


class VerificationConfig(BaseModel):
    smoke_test: str = "required"
    stable_after: StableAfterConfig

    @field_validator("smoke_test")
    @classmethod
    def validate_smoke_test(cls, value: str) -> str:
        allowed = {"required", "optional", "disabled"}
        if value not in allowed:
            msg = f"smoke_test must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value


class CompletionConfig(BaseModel):
    condition: str = Field(min_length=1)
    on_complete: list[str] = Field(default_factory=list)


class EscalationConfig(BaseModel):
    channel: str = "file"
    blocking_timeout: DurationField
    on_timeout: str = "stop"

    @field_validator("blocking_timeout", mode="before")
    @classmethod
    def parse_blocking_timeout(cls, value: Any) -> Duration:
        return parse_duration(value)

    @field_validator("on_timeout")
    @classmethod
    def validate_on_timeout(cls, value: str) -> str:
        allowed = {"stop", "continue", "escalate"}
        if value not in allowed:
            msg = f"on_timeout must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return value


class RuntimeLoaderConfig(BaseModel):
    """How the harness loads this project's RuntimeAdapter."""

    plugin: str | None = Field(
        default=None,
        description="Registered entry point name (research_harness.runtimes group).",
    )
    entrypoint: str | None = Field(
        default=None,
        description="Explicit module:callable runtime factory.",
    )
    options: dict[str, Any] = Field(default_factory=dict)


class ExperimentConfig(BaseModel):
    """Optional pointers to methodology artifacts the harness can reconcile against.

    Absent ``experiment:`` keeps v1.1 behavior (``validity.expected_units`` only).
    When present, ``plan`` is a path to harness-owned ``experiment/plan.json``.
    """

    plan: str = Field(min_length=1, description="Path to experiment/plan.json")
    schedule: str | None = Field(
        default=None,
        description="Optional path to schedule.csv (run order / cells).",
    )


class ProjectContract(BaseModel):
    """Enhanced project contract schema v1.1."""

    contract_version: int = Field(ge=1)
    project: ProjectInfo
    invariants: list[Invariant] = Field(default_factory=list)
    fingerprint: FingerprintConfig
    progress: ProgressConfig
    validity: ValidityConfig
    authority: AuthorityConfig
    budget: BudgetConfig
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    verification: VerificationConfig
    completion: CompletionConfig
    escalation: EscalationConfig
    runtime_loader: RuntimeLoaderConfig | None = None
    experiment: ExperimentConfig | None = None

    @field_validator("contract_version")
    @classmethod
    def validate_contract_version(cls, value: int) -> int:
        if value != 1:
            msg = f"Unsupported contract_version {value}; only version 1 is supported"
            raise ValueError(msg)
        return value

    def model_dump_yaml(self) -> dict[str, Any]:
        """Serialize to a YAML-friendly dict with duration strings."""
        return self.model_dump(mode="json")
