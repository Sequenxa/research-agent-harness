from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from research_harness.models.mutation import MutationReadiness
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
