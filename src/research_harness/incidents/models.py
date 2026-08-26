from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IncidentStage(StrEnum):
    DETECT = "DETECT"
    DIAGNOSE = "DIAGNOSE"
    HYPOTHESIZE = "HYPOTHESIZE"
    REMEDIATE = "REMEDIATE"
    RELAUNCH = "RELAUNCH"
    VERIFY = "VERIFY"
    BURN_IN = "BURN_IN"
    RESUME = "RESUME"
    CLOSE = "CLOSE"


class IncidentRecordStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Incident(BaseModel):
    incident_id: str
    project_id: str
    contract_version: int
    symptom: str
    stage: IncidentStage = IncidentStage.DETECT
    status: IncidentRecordStatus = IncidentRecordStatus.OPEN
    opened_at: datetime
    closed_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediations: list[str] = Field(default_factory=list)
    resolution: str | None = None
