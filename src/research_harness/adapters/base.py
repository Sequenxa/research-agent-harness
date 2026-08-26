from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from research_harness.models.mutation import MutationReadiness, MutationRepairResult
from research_harness.models.state import ObservedState


class RuntimeAdapter(ABC):
    """Inspect and control the running research workload."""

    @abstractmethod
    def inspect(self) -> ObservedState:
        """Return current runtime observations."""

    @abstractmethod
    def restart_worker(self) -> None:
        """Restart the worker process or equivalent."""

    @abstractmethod
    def relaunch(self, action: str) -> None:
        """Apply a contract-declared relaunch action."""

    def mutation_preflight(self, action: str) -> MutationReadiness:
        """Project-specific safety gate before applying a mutation.

        Override in project adapters to enforce native authorization,
        safe-window boundaries, checkpoint preservation, etc.
        Default: mutations are permitted when authority allows.
        """
        return MutationReadiness.ready(action)

    def repair_mutation_prerequisite(self, repair_id: str) -> MutationRepairResult:
        """Apply and verify one permitted prerequisite repair before mutation.

        Override when ``mutation_preflight`` returns REPAIRABLE with repairs.
        Each repair must verify it took effect — do not assume exit code 0 means success.
        """
        return MutationRepairResult.failed(
            repair_id,
            detail=f"repair not implemented: {repair_id}",
        )

    def fingerprint_field_classifications(self) -> dict[str, str]:
        """Classify fingerprint fields for deployment promotion policy.

        Values: deployment | research_semantic | authorization_sensitive
        """
        return {}


class CheckpointAdapter(ABC):
    """Read and write experiment checkpoints."""

    @abstractmethod
    def latest_checkpoint(self) -> dict[str, Any] | None:
        """Return the latest checkpoint metadata, if any."""

    @abstractmethod
    def save_checkpoint(self, payload: dict[str, Any]) -> str:
        """Persist a checkpoint and return its identifier."""


class DiagnosticsAdapter(ABC):
    """Collect evidence for incidents and recovery decisions."""

    @abstractmethod
    def collect(self, *, symptom: str) -> dict[str, Any]:
        """Gather diagnostic evidence for a symptom."""
