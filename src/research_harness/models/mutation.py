from __future__ import annotations

from pydantic import BaseModel, Field

from research_harness.models.enums import MutationReadinessStatus


class MutationPreflightCheck(BaseModel):
    """One project-specific gate for a contemplated mutation."""

    name: str
    passed: bool
    detail: str | None = None


class MutationRepair(BaseModel):
    """A permitted prerequisite fix the harness may apply before mutating."""

    repair_id: str
    description: str | None = None


class MutationRepairResult(BaseModel):
    """Outcome of applying and verifying one prerequisite repair."""

    repair_id: str
    succeeded: bool
    verified: bool
    detail: str | None = None

    @classmethod
    def ok(cls, repair_id: str, *, detail: str | None = None) -> MutationRepairResult:
        return cls(repair_id=repair_id, succeeded=True, verified=True, detail=detail)

    @classmethod
    def failed(cls, repair_id: str, *, detail: str) -> MutationRepairResult:
        return cls(repair_id=repair_id, succeeded=False, verified=False, detail=detail)


class MutationReadiness(BaseModel):
    """Whether the harness may safely apply a runtime mutation."""

    status: MutationReadinessStatus
    action: str
    checks: list[MutationPreflightCheck] = Field(default_factory=list)
    repairs: list[MutationRepair] = Field(default_factory=list)
    reason: str | None = None

    @classmethod
    def ready(cls, action: str) -> MutationReadiness:
        return cls(status=MutationReadinessStatus.READY, action=action)

    @classmethod
    def repairable(
        cls,
        action: str,
        *,
        reason: str,
        repairs: list[MutationRepair],
        checks: list[MutationPreflightCheck] | None = None,
    ) -> MutationReadiness:
        return cls(
            status=MutationReadinessStatus.REPAIRABLE,
            action=action,
            reason=reason,
            repairs=list(repairs),
            checks=list(checks or ()),
        )

    @classmethod
    def blocked(
        cls,
        action: str,
        *,
        reason: str,
        checks: list[MutationPreflightCheck] | None = None,
    ) -> MutationReadiness:
        return cls(
            status=MutationReadinessStatus.BLOCKED,
            action=action,
            reason=reason,
            checks=list(checks or ()),
        )
