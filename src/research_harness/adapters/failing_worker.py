from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_harness.adapters.base import (
    CheckpointAdapter,
    DiagnosticsAdapter,
    RuntimeAdapter,
)
from research_harness.models.enums import Health, Lifecycle, Progress, RuntimeFreshness
from research_harness.models.state import ObservedState
from research_harness.watchdog.evaluator import ProgressContext, WatermarkObservation


@dataclass
class WorkerConfig:
    model: str = "gpt-4o-mini"
    provider: str = "openai"
    prompt_version: str = "v1"
    dataset_version: str = "d1"
    evaluator_version: str = "e1"
    git_sha: str = "dev"
    lock_hash: str = "lock"
    config_hash: str = "cfg-a"
    total_units: int = 100
    crash_at_units: list[int] = field(default_factory=lambda: [15, 40])
    stall_at_units: list[int] = field(default_factory=lambda: [25])
    negative_result_at_unit: int = 80
    effect_size_threshold: float = 0.05
    seed: int = 42

    def fingerprint(self) -> dict[str, str]:
        return {
            "git_sha": self.git_sha,
            "lock_hash": self.lock_hash,
            "model": self.model,
            "provider": self.provider,
            "prompt_version": self.prompt_version,
            "dataset_version": self.dataset_version,
            "evaluator_version": self.evaluator_version,
            "config_hash": self.config_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerConfig:
        return cls(**{key: data[key] for key in asdict(cls()) if key in data})


@dataclass
class WorkerState:
    running: bool = True
    completed_units: int = 0
    last_progress_at: str | None = None
    last_heartbeat_at: str | None = None
    cumulative_effect: float = 0.0
    scientific_result_recorded: bool = False
    pending_config_hash: str | None = None
    restart_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerState:
        return cls(
            running=bool(data.get("running", True)),
            completed_units=int(data.get("completed_units", 0)),
            last_progress_at=data.get("last_progress_at"),
            last_heartbeat_at=data.get("last_heartbeat_at"),
            cumulative_effect=float(data.get("cumulative_effect", 0.0)),
            scientific_result_recorded=bool(data.get("scientific_result_recorded", False)),
            pending_config_hash=data.get("pending_config_hash"),
            restart_count=int(data.get("restart_count", 0)),
        )


class FailingWorkerStore:
    """Persist worker config, runtime state, and checkpoints on disk."""

    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "config.json"
        self.state_path = self.state_dir / "worker_state.json"
        self.checkpoint_path = self.state_dir / "checkpoint.json"

    def load_config(self) -> WorkerConfig:
        if not self.config_path.exists():
            config = WorkerConfig()
            self.save_config(config)
            return config
        data: Any = json.loads(self.config_path.read_text(encoding="utf-8"))
        return WorkerConfig.from_dict(data)

    def save_config(self, config: WorkerConfig) -> None:
        self.config_path.write_text(
            json.dumps(asdict(config), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def load_state(self) -> WorkerState:
        if not self.state_path.exists():
            state = WorkerState()
            self.save_state(state)
            return state
        data: Any = json.loads(self.state_path.read_text(encoding="utf-8"))
        return WorkerState(
            running=bool(data.get("running", True)),
            completed_units=int(data.get("completed_units", 0)),
            last_progress_at=data.get("last_progress_at"),
            last_heartbeat_at=data.get("last_heartbeat_at"),
            cumulative_effect=float(data.get("cumulative_effect", 0.0)),
            scientific_result_recorded=bool(data.get("scientific_result_recorded", False)),
            pending_config_hash=data.get("pending_config_hash"),
            restart_count=int(data.get("restart_count", 0)),
        )

    def save_state(self, state: WorkerState) -> None:
        self.state_path.write_text(
            json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def load_checkpoint(self) -> dict[str, Any]:
        if not self.checkpoint_path.exists():
            return {"completed_units": 0}
        data: Any = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        return dict(data)

    def save_checkpoint(self, payload: dict[str, Any]) -> None:
        self.checkpoint_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class FailingWorkerRuntime(RuntimeAdapter, CheckpointAdapter, DiagnosticsAdapter):
    """File-backed failing worker used by examples and acceptance tests."""

    def __init__(self, *, project_id: str, state_dir: Path | str) -> None:
        self.project_id = project_id
        self.store = FailingWorkerStore(state_dir)
        self._rng = random.Random(self.store.load_config().seed)

    def inspect(self) -> ObservedState:
        config = self.store.load_config()
        state = self.store.load_state()
        observed_at = datetime.now(UTC)
        health = Health.HEALTHY if state.running else Health.UNHEALTHY
        lifecycle = Lifecycle.RUNNING if state.running else Lifecycle.STOPPED
        progress = Progress.ADVANCING if state.running else Progress.STALLED
        last_progress = (
            datetime.fromisoformat(state.last_progress_at)
            if state.last_progress_at
            else None
        )
        last_heartbeat = (
            datetime.fromisoformat(state.last_heartbeat_at)
            if state.last_heartbeat_at
            else None
        )
        fingerprint = config.fingerprint()
        freshness = (
            RuntimeFreshness.STALE
            if state.pending_config_hash is not None
            and state.pending_config_hash != config.config_hash
            else RuntimeFreshness.CURRENT
        )
        return ObservedState(
            project_id=self.project_id,
            observed_at=observed_at,
            lifecycle=lifecycle,
            health=health,
            progress=progress,
            runtime_freshness=freshness,
            fingerprint=fingerprint,
            completed_units=state.completed_units,
            last_progress_at=last_progress,
            last_checkpoint_at=self.store.load_checkpoint().get("saved_at"),
            extra={
                "worker_heartbeat_last_at": last_heartbeat,
                "cumulative_effect": state.cumulative_effect,
                "scientific_result_recorded": state.scientific_result_recorded,
            },
        )

    def progress_context(self) -> ProgressContext:
        observed = self.inspect()
        return ProgressContext(
            watermarks={
                "completed_units": WatermarkObservation(
                    name="completed_units",
                    value=observed.completed_units,
                    last_advanced_at=observed.last_progress_at,
                ),
                "worker_heartbeat": WatermarkObservation(
                    name="worker_heartbeat",
                    value=1 if observed.health == Health.HEALTHY else 0,
                    last_advanced_at=observed.extra.get("worker_heartbeat_last_at"),
                ),
            }
        )

    def restart_worker(self) -> None:
        state = self.store.load_state()
        checkpoint = self.store.load_checkpoint()
        state.running = True
        state.completed_units = int(checkpoint.get("completed_units", state.completed_units))
        now = datetime.now(UTC).isoformat()
        state.last_progress_at = now
        state.last_heartbeat_at = now
        state.restart_count += 1
        self.store.save_state(state)

    def relaunch(self, action: str) -> None:
        del action
        config = self.store.load_config()
        state = self.store.load_state()
        if state.pending_config_hash is not None:
            config.config_hash = state.pending_config_hash
            self.store.save_config(config)
            state.pending_config_hash = None
        self.store.save_config(config)
        self.restart_worker()

    def latest_checkpoint(self) -> dict[str, Any] | None:
        checkpoint = self.store.load_checkpoint()
        return dict(checkpoint)

    def save_checkpoint(self, payload: dict[str, Any]) -> str:
        data = dict(payload)
        data["saved_at"] = datetime.now(UTC).isoformat()
        self.store.save_checkpoint(data)
        return "checkpoint-1"

    def set_pending_config(self, config_hash: str) -> None:
        state = self.store.load_state()
        state.pending_config_hash = config_hash
        self.store.save_state(state)

    def process_units(self, count: int = 1) -> WorkerState:
        config = self.store.load_config()
        state = self.store.load_state()
        if not state.running:
            return state

        for _ in range(count):
            if state.completed_units >= config.total_units:
                break
            state.completed_units += 1
            draw = self._rng.uniform(-0.02, 0.12)
            state.cumulative_effect += draw
            now = datetime.now(UTC).isoformat()
            state.last_progress_at = now
            state.last_heartbeat_at = now

            if state.completed_units in config.crash_at_units:
                state.running = False
                break
            if state.completed_units in config.stall_at_units:
                stale = datetime.now(UTC).replace(year=2000).isoformat()
                state.last_progress_at = stale
                state.last_heartbeat_at = stale
                break

        self.store.save_checkpoint(
            {
                "completed_units": state.completed_units,
                "cumulative_effect": state.cumulative_effect,
            }
        )
        self.store.save_state(state)
        return state

    def current_effect_size(self) -> float:
        state = self.store.load_state()
        if state.completed_units == 0:
            return 0.0
        return state.cumulative_effect / state.completed_units

    def collect(self, *, symptom: str) -> dict[str, Any]:
        config = self.store.load_config()
        state = self.store.load_state()
        checkpoint = self.store.load_checkpoint()
        return {
            "symptom": symptom,
            "running": state.running,
            "completed_units": state.completed_units,
            "restart_count": state.restart_count,
            "config_hash": config.config_hash,
            "pending_config_hash": state.pending_config_hash,
            "checkpoint_units": checkpoint.get("completed_units"),
            "effect_size": self.current_effect_size(),
        }

    def stop(self) -> None:
        state = self.store.load_state()
        state.running = False
        self.store.save_state(state)
