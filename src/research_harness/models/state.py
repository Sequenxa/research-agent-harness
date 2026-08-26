from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from research_harness.models.enums import (
    Health,
    IncidentStatus,
    InspectionStatus,
    Lifecycle,
    Progress,
    RuntimeFreshness,
    VerificationLevel,
)


class DesiredState(BaseModel):
    """What the project should look like according to contract and configuration."""

    project_id: str
    contract_version: int
    lifecycle: Lifecycle = Lifecycle.INITIALIZING
    fingerprint: dict[str, str] = Field(default_factory=dict)
    completion_condition: str
    extra: dict[str, Any] = Field(default_factory=dict)


class ObservedState(BaseModel):
    """What the runtime actually looks like right now."""

    project_id: str
    observed_at: datetime
    lifecycle: Lifecycle = Lifecycle.INITIALIZING
    health: Health = Health.UNHEALTHY
    progress: Progress = Progress.STALLED
    runtime_freshness: RuntimeFreshness = RuntimeFreshness.STALE
    inspection: InspectionStatus = InspectionStatus.AVAILABLE
    incident_status: IncidentStatus = IncidentStatus.NONE
    verification_level: VerificationLevel | None = None
    fingerprint: dict[str, str] = Field(default_factory=dict)
    completed_units: int = 0
    last_progress_at: datetime | None = None
    last_checkpoint_at: datetime | None = None
    current_incident_id: str | None = None
    spend_usd: float = 0.0
    extra: dict[str, Any] = Field(default_factory=dict)


class ReconciliationDifference(BaseModel):
    field: str
    desired: Any
    observed: Any
    action: str | None = None


class ReconciliationResult(BaseModel):
    """Outcome of comparing desired and observed state."""

    project_id: str
    reconciled_at: datetime
    differences: list[ReconciliationDifference] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    success: bool = False
    blocked_reason: str | None = None
