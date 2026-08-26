from __future__ import annotations

from enum import StrEnum


class Lifecycle(StrEnum):
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class Health(StrEnum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"


class Progress(StrEnum):
    ADVANCING = "ADVANCING"
    STALLED = "STALLED"


class RuntimeFreshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class InspectionStatus(StrEnum):
    """Whether the harness can inspect runtime state."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class MutationReadinessStatus(StrEnum):
    """Whether a contemplated runtime mutation is permitted now."""

    READY = "READY"
    REPAIRABLE = "REPAIRABLE"
    BLOCKED = "BLOCKED"


class FingerprintFieldClass(StrEnum):
    """How a fingerprint field should be treated during deployment promotion."""

    DEPLOYMENT = "deployment"
    RESEARCH_SEMANTIC = "research_semantic"
    AUTHORIZATION_SENSITIVE = "authorization_sensitive"


class IncidentStatus(StrEnum):
    NONE = "NONE"
    OPEN = "OPEN"
    RECOVERING = "RECOVERING"
    VERIFYING = "VERIFYING"


class VerificationLevel(StrEnum):
    PATCHED = "PATCHED"
    VERIFIED = "VERIFIED"
    STABLE = "STABLE"
