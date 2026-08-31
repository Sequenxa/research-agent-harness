from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerStore
from research_harness.models.enums import (
    Health,
    Lifecycle,
    MutationReadinessStatus,
    Progress,
    RuntimeFreshness,
)
from research_harness.models.mutation import MutationReadiness
from research_harness.models.state import ObservedState
from research_harness.reconciliation.engine import Reconciler
from research_harness.runtime.assessment import assess_operation, resolve_fingerprint_state
from research_harness.runtime.fingerprint_state import FingerprintState
from research_harness.runtime.io import write_fingerprint_file


class ThreeWayRuntime(RuntimeAdapter):
    def __init__(
        self,
        *,
        project_id: str,
        running: dict[str, str],
        repository: dict[str, str] | None = None,
        block_actions: set[str] | None = None,
    ) -> None:
        self.project_id = project_id
        self.running = dict(running)
        self._repository = dict(repository) if repository is not None else None
        self.block_actions = block_actions or set()
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

    def repository_fingerprint(self) -> dict[str, str] | None:
        return dict(self._repository) if self._repository is not None else None

    def mutation_preflight(self, action: str) -> MutationReadiness:
        if action in self.block_actions:
            return MutationReadiness.blocked(action, reason=f"blocked:{action}")
        return MutationReadiness.ready(action)

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


def test_fingerprint_state_repo_ahead_does_not_require_reconciliation() -> None:
    state = FingerprintState(
        running=_fields(config_hash="cfg-a"),
        desired=_fields(config_hash="cfg-a"),
        repository=_fields(config_hash="cfg-b"),
    )
    contract = default_contract(project_id="demo", objective="obj")

    assert state.repository_ahead_of_desired(fields=contract.fingerprint.fields) is True
    assert state.reconciliation_required(fields=contract.fingerprint.fields) is False
    assert state.runtime_freshness(fields=contract.fingerprint.fields) == RuntimeFreshness.CURRENT


def test_fingerprint_state_stale_when_desired_differs_from_running() -> None:
    state = FingerprintState(
        running=_fields(config_hash="cfg-a"),
        desired=_fields(config_hash="cfg-b"),
        repository=_fields(config_hash="cfg-b"),
    )
    contract = default_contract(project_id="demo", objective="obj")

    assert state.reconciliation_required(fields=contract.fingerprint.fields) is True
    assert state.runtime_freshness(fields=contract.fingerprint.fields) == RuntimeFreshness.STALE


def test_desired_defaults_to_running_without_explicit_promotion(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    runtime = ThreeWayRuntime(
        project_id="demo",
        running=_fields(config_hash="cfg-a"),
        repository=_fields(config_hash="cfg-b"),
    )
    fingerprints = resolve_fingerprint_state(
        runtime=runtime,
        contract=contract,
        state_dir=tmp_path,
        runtime_kind="file",
    )
    assert fingerprints.desired == fingerprints.running
    assert fingerprints.repository_ahead_of_desired(fields=contract.fingerprint.fields) is True
    assert fingerprints.reconciliation_required(fields=contract.fingerprint.fields) is False


def test_explicit_desired_enables_reconciliation(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    write_fingerprint_file(tmp_path / "desired_fingerprint.json", _fields(config_hash="cfg-b"))
    runtime = ThreeWayRuntime(
        project_id="demo",
        running=_fields(config_hash="cfg-a"),
        repository=_fields(config_hash="cfg-b"),
    )
    fingerprints = resolve_fingerprint_state(
        runtime=runtime,
        contract=contract,
        state_dir=tmp_path,
        runtime_kind="file",
    )
    assert fingerprints.reconciliation_required(fields=contract.fingerprint.fields) is True


def test_reconcile_blocked_by_mutation_preflight(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    desired = _fields(prompt_version="v2")
    observed = _fields(prompt_version="v1")
    runtime = ThreeWayRuntime(
        project_id="demo",
        running=observed,
        block_actions={"worker_restart"},
    )
    runtime._pending_fingerprint = desired  # noqa: SLF001
    ledger = LedgerStore(tmp_path / "ledger.db")
    reconciler = Reconciler(contract=contract, runtime=runtime, ledger=ledger)

    result = reconciler.reconcile(desired_fingerprint=desired)

    assert result.success is False
    assert result.blocked_reason is not None
    assert "blocked:worker_restart" in result.blocked_reason
    assert runtime.relaunches == []


def test_assess_operation_reports_orthogonal_signals(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    runtime = ThreeWayRuntime(
        project_id="demo",
        running=_fields(config_hash="cfg-a"),
        repository=_fields(config_hash="cfg-b"),
    )
    assessment = assess_operation(
        runtime=runtime,
        contract=contract,
        state_dir=tmp_path,
        runtime_kind="file",
        mutation_action="full_relaunch",
    )
    assert assessment.runtime_health == Health.HEALTHY
    assert assessment.runtime_freshness == RuntimeFreshness.CURRENT
    assert assessment.repository_ahead is True
    assert assessment.mutation is not None
    assert assessment.mutation.status == MutationReadinessStatus.READY
