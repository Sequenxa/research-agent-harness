from __future__ import annotations

from pydantic import BaseModel, Field

from research_harness.models.enums import MutationReadinessStatus


class MutationPreflightCheck(BaseModel):
    """One project-specific gate for a contemplated mutation."""

    name: str
    passed: bool
    detail: str | None = None


class MutationReadiness(BaseModel):
    """Whether the harness may safely apply a runtime mutation."""

    status: MutationReadinessStatus
    action: str
    checks: list[MutationPreflightCheck] = Field(default_factory=list)
    reason: str | None = None

    @classmethod
    def ready(cls, action: str) -> MutationReadiness:
        return cls(status=MutationReadinessStatus.READY, action=action)

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

    @classmethod
    def wait(
        cls,
        action: str,
        *,
        reason: str,
        checks: list[MutationPreflightCheck] | None = None,
    ) -> MutationReadiness:
        return cls(
            status=MutationReadinessStatus.WAIT,
            action=action,
            reason=reason,
            checks=list(checks or ()),
        )
