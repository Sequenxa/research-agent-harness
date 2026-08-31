from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from research_harness.adapters.base import RuntimeAdapter
from research_harness.adapters.failing_worker import FailingWorkerRuntime
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerStore
from research_harness.models.enums import Health, Lifecycle, MutationReadinessStatus, Progress
from research_harness.models.mutation import MutationReadiness, MutationRepair, MutationRepairResult
from research_harness.models.state import ObservedState
from research_harness.reconciliation.engine import Reconciler
from research_harness.runtime.mutation import remediate_preflight


class RepairableRuntime(RuntimeAdapter):
    def __init__(
        self,
        *,
        project_id: str,
        running: dict[str, str],
        repairable_actions: set[str] | None = None,
        block_actions: set[str] | None = None,
    ) -> None:
        self.project_id = project_id
        self.running = dict(running)
        self.repairable_actions = repairable_actions or set()
        self.block_actions = block_actions or set()
        self.authorization_ok = False
        self.repairs_applied: list[str] = []
        self.relaunches: list[str] = []

    def inspect(self) -> ObservedState:
        lifecycle = Lifecycle.STOPPED if getattr(self, "_stopped", False) else Lifecycle.RUNNING
        progress = Progress.STALLED if getattr(self, "_stopped", False) else Progress.ADVANCING
        health = Health.UNHEALTHY if getattr(self, "_stopped", False) else Health.HEALTHY
        return ObservedState(
            project_id=self.project_id,
            observed_at=datetime.now(UTC),
            lifecycle=lifecycle,
            health=health,
            progress=progress,
            fingerprint=dict(self.running),
            completed_units=1,
        )

    def mutation_preflight(self, action: str) -> MutationReadiness:
        if action in self.block_actions:
            return MutationReadiness.blocked(action, reason=f"blocked:{action}")
        if action in self.repairable_actions and not self.authorization_ok:
            return MutationReadiness.repairable(
                action,
                reason="authorization must be refreshed",
                repairs=[
                    MutationRepair(
                        repair_id="refresh_scheduler_authorization",
                        description="Rebuild scheduler authorization",
                    )
                ],
            )
        return MutationReadiness.ready(action)

    def repair_mutation_prerequisite(self, repair_id: str) -> MutationRepairResult:
        if repair_id != "refresh_scheduler_authorization":
            return MutationRepairResult.failed(repair_id, detail="unknown repair")
        self.authorization_ok = True
        self.repairs_applied.append(repair_id)
        return MutationRepairResult.ok(repair_id)

    def restart_worker(self) -> None:
        return None

    def stop(self) -> None:
        self._stopped = True

    def relaunch(self, action: str) -> None:
        self.relaunches.append(action)
        if hasattr(self, "_pending_fingerprint"):
            self.running = dict(self._pending_fingerprint)


def _fields(**overrides: str) -> dict[str, str]:
    base = {
        "git_sha": "abc",
        "lock_hash": "lock",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "prompt_version": "v1",
        "dataset_version": "d1",
        "evaluator_version": "e1",
        "config_hash": "cfg-a",
    }
    base.update(overrides)
    return base


def test_remediate_preflight_applies_repairs() -> None:
    runtime = RepairableRuntime(
        project_id="demo",
        running=_fields(),
        repairable_actions={"full_relaunch"},
    )
    result = remediate_preflight(runtime, "full_relaunch")
    assert result.final.status == MutationReadinessStatus.READY
    assert result.repairs_applied == ["refresh_scheduler_authorization"]
    assert runtime.authorization_ok is True


def test_remediate_preflight_stays_blocked() -> None:
    runtime = RepairableRuntime(
        project_id="demo",
        running=_fields(),
        block_actions={"full_relaunch"},
    )
    result = remediate_preflight(runtime, "full_relaunch")
    assert result.final.status == MutationReadinessStatus.BLOCKED
    assert result.repairs_applied == []


def test_reconcile_repairable_then_relaunches(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    desired = _fields(prompt_version="v2", config_hash="cfg-b")
    observed = _fields(prompt_version="v1", config_hash="cfg-a")
    runtime = RepairableRuntime(
        project_id="demo",
        running=observed,
        repairable_actions={"worker_restart"},
    )
    runtime._pending_fingerprint = desired  # noqa: SLF001
    ledger = LedgerStore(tmp_path / "ledger.db")
    reconciler = Reconciler(contract=contract, runtime=runtime, ledger=ledger)

    result = reconciler.reconcile(desired_fingerprint=desired)

    assert result.success is True
    assert "repair:refresh_scheduler_authorization" in result.actions_taken
    assert "worker_restart" in result.actions_taken
    assert runtime.repairs_applied == ["refresh_scheduler_authorization"]
    assert runtime.relaunches == ["worker_restart"]
    assert result.deployment_delta is not None
    assert result.deployment_delta.required_action == "worker_restart"
    assert set(result.deployment_delta.changed_fields) == {"prompt_version", "config_hash"}


def test_failing_worker_repairable_authorization_refresh(tmp_path: Any) -> None:
    runtime = FailingWorkerRuntime(project_id="demo", state_dir=tmp_path)
    runtime.set_pending_config("cfg-b")
    preflight = runtime.mutation_preflight("full_relaunch")
    assert preflight.status == MutationReadinessStatus.REPAIRABLE
    remediation = remediate_preflight(runtime, "full_relaunch")
    assert remediation.final.status == MutationReadinessStatus.READY
    state = runtime.store.load_state()
    assert state.authorized_config_hash == "cfg-b"
    assert "cfg-a" in state.authorization_history
