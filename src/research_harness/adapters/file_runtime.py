from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_harness.adapters.base import RuntimeAdapter
from research_harness.models.enums import Health, Lifecycle, Progress, RuntimeFreshness
from research_harness.models.state import ObservedState


class FileRuntimeAdapter(RuntimeAdapter):
    """Local file-backed runtime adapter for harness development and Slice 2 demos.

    Observed fingerprint lives in ``state_dir / observed_fingerprint.json``.
    ``relaunch`` copies the pending desired fingerprint into the observed file.
    """

    def __init__(
        self,
        *,
        project_id: str,
        state_dir: Path,
        pending_desired: dict[str, str] | None = None,
    ) -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.observed_path = self.state_dir / "observed_fingerprint.json"
        self._pending_desired = pending_desired

    def inspect(self) -> ObservedState:
        fingerprint = self._read_fingerprint(self.observed_path)
        return ObservedState(
            project_id=self.project_id,
            observed_at=datetime.now(UTC),
            lifecycle=Lifecycle.RUNNING if fingerprint else Lifecycle.STOPPED,
            health=Health.HEALTHY if fingerprint else Health.UNHEALTHY,
            progress=Progress.ADVANCING if fingerprint else Progress.STALLED,
            runtime_freshness=RuntimeFreshness.STALE,
            fingerprint=fingerprint,
            completed_units=0,
        )

    def restart_worker(self) -> None:
        self.relaunch("worker_restart")

    def relaunch(self, action: str) -> None:
        del action  # action semantics are recorded by the reconciler; file adapter applies desired.
        if self._pending_desired is None:
            msg = "No pending desired fingerprint set before relaunch"
            raise RuntimeError(msg)
        self.write_observed(self._pending_desired)

    def set_pending_desired(self, fingerprint: dict[str, str]) -> None:
        self._pending_desired = dict(fingerprint)

    def write_observed(self, fingerprint: dict[str, str]) -> None:
        self.observed_path.write_text(
            json.dumps(fingerprint, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_fingerprint(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            msg = f"Fingerprint file must contain a JSON object: {path}"
            raise ValueError(msg)
        return {str(key): str(value) for key, value in data.items()}
