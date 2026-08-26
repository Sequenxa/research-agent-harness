from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from research_harness.adapters.base import CheckpointAdapter, RuntimeAdapter
from research_harness.models.enums import Health, Lifecycle, Progress, RuntimeFreshness
from research_harness.models.state import ObservedState
from research_harness.watchdog.evaluator import ProgressContext, WatermarkObservation


class FakeWorker(RuntimeAdapter, CheckpointAdapter):
    """Deterministic in-memory worker for acceptance tests and local demos."""

    def __init__(self, *, project_id: str, fingerprint: dict[str, str] | None = None) -> None:
        self.project_id = project_id
        self.fingerprint = dict(fingerprint or {})
        self.running = True
        self.completed_units = 0
        self.last_progress_at = datetime.now(UTC)
        self.last_heartbeat_at = datetime.now(UTC)
        self._checkpoint: dict[str, Any] = {"completed_units": 0}
        self.restart_count = 0

    def inspect(self) -> ObservedState:
        health = Health.HEALTHY if self.running else Health.UNHEALTHY
        lifecycle = Lifecycle.RUNNING if self.running else Lifecycle.STOPPED
        progress = Progress.ADVANCING if self.running else Progress.STALLED
        return ObservedState(
            project_id=self.project_id,
            observed_at=datetime.now(UTC),
            lifecycle=lifecycle,
            health=health,
            progress=progress,
            runtime_freshness=RuntimeFreshness.CURRENT,
            fingerprint=dict(self.fingerprint),
            completed_units=self.completed_units,
            last_progress_at=self.last_progress_at,
            last_checkpoint_at=self._checkpoint.get("saved_at"),
            extra={"worker_heartbeat_last_at": self.last_heartbeat_at},
        )

    def progress_context(self) -> ProgressContext:
        return ProgressContext(
            watermarks={
                "completed_units": WatermarkObservation(
                    name="completed_units",
                    value=self.completed_units,
                    last_advanced_at=self.last_progress_at,
                ),
                "worker_heartbeat": WatermarkObservation(
                    name="worker_heartbeat",
                    value=1 if self.running else 0,
                    last_advanced_at=self.last_heartbeat_at,
                ),
            }
        )

    def restart_worker(self) -> None:
        self.restart_count += 1
        self.running = True
        self.completed_units = int(self._checkpoint.get("completed_units", 0))
        now = datetime.now(UTC)
        self.last_progress_at = now
        self.last_heartbeat_at = now

    def relaunch(self, action: str) -> None:
        del action
        self.restart_worker()

    def latest_checkpoint(self) -> dict[str, Any] | None:
        return dict(self._checkpoint)

    def save_checkpoint(self, payload: dict[str, Any]) -> str:
        self._checkpoint = dict(payload)
        self._checkpoint["saved_at"] = datetime.now(UTC).isoformat()
        return "checkpoint-1"

    def crash(self) -> None:
        self.running = False

    def stall(self) -> None:
        """Keep process alive but stop advancing watermarks."""
        self.running = True
        stale = datetime.now(UTC) - timedelta(hours=1)
        self.last_progress_at = stale
        self.last_heartbeat_at = stale

    def tick(self, units: int = 1) -> None:
        if not self.running:
            return
        self.completed_units += units
        now = datetime.now(UTC)
        self.last_progress_at = now
        self.last_heartbeat_at = now
        self._checkpoint = {
            "completed_units": self.completed_units,
            "saved_at": now.isoformat(),
        }
