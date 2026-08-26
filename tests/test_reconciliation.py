from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import FingerprintFieldClass, Health, Lifecycle, Progress, RuntimeFreshness
from research_harness.models.state import ObservedState
from research_harness.reconciliation.engine import Reconciler
from research_harness.runtime.desired import build_desired_state, merge_repository_deployment_fields
from research_harness.runtime.io import load_fingerprint_file, write_fingerprint_file


class FakeRuntimeAdapter(RuntimeAdapter):
    def __init__(self, *, project_id: str, fingerprint: dict[str, str]) -> None:
        self.project_id = project_id
        self.fingerprint = dict(fingerprint)
        self.relaunches: list[str] = []
        self.restarts = 0

    def inspect(self) -> ObservedState:
        return ObservedState(
            project_id=self.project_id,
            observed_at=datetime.now(UTC),
            lifecycle=Lifecycle.RUNNING,
            health=Health.HEALTHY,
            progress=Progress.ADVANCING,
            runtime_freshness=RuntimeFreshness.STALE,
            fingerprint=dict(self.fingerprint),
            completed_units=10,
        )

    def restart_worker(self) -> None:
        self.restarts += 1

    def relaunch(self, action: str) -> None:
        self.relaunches.append(action)
        # After relaunch, runtime adopts desired fingerprint from last reconcile.
        if hasattr(self, "_pending_fingerprint"):
            self.fingerprint = dict(self._pending_fingerprint)


def test_build_desired_state_from_contract_and_fields() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    fields = {
        "git_sha": "abc",
        "lock_hash": "lock",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "prompt_version": "v1",
        "dataset_version": "d1",
        "evaluator_version": "e1",
        "config_hash": "cfg",
    }
    desired = build_desired_state(contract, fingerprint_fields=fields)
    assert desired.project_id == "demo"
    assert desired.fingerprint["model"] == "gpt-4o-mini"
    assert desired.lifecycle == Lifecycle.RUNNING


def test_reconcile_detects_stale_and_relaunches(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    desired_fields = {
        "git_sha": "abc",
        "lock_hash": "lock",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "prompt_version": "v2",
        "dataset_version": "d1",
        "evaluator_version": "e1",
        "config_hash": "cfg",
    }
    observed_fields = dict(desired_fields)
    observed_fields["prompt_version"] = "v1"

    runtime = FakeRuntimeAdapter(project_id="demo", fingerprint=observed_fields)
    runtime._pending_fingerprint = desired_fields  # noqa: SLF001
    ledger = LedgerStore(tmp_path / "ledger.db")
    reconciler = Reconciler(contract=contract, runtime=runtime, ledger=ledger)

    result = reconciler.reconcile(desired_fingerprint=desired_fields)

    assert result.success is True
    assert result.blocked_reason is None
    assert "worker_restart" in result.actions_taken
    assert runtime.relaunches == ["worker_restart"]
    assert any(d.field == "fingerprint.prompt_version" for d in result.differences)

    events = ledger.list_events(project_id="demo")
    assert any(e.event_type == LedgerEventType.RUNTIME_RECONCILIATION for e in events)

    # After relaunch, fingerprint should match.
    post = runtime.inspect()
    assert post.fingerprint["prompt_version"] == "v2"


def test_reconcile_blocks_when_restarts_not_authorized(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    contract.authority.runtime_restarts = False
    desired_fields = {
        "git_sha": "abc",
        "lock_hash": "lock",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "prompt_version": "v2",
        "dataset_version": "d1",
        "evaluator_version": "e1",
        "config_hash": "cfg",
    }
    observed_fields = dict(desired_fields)
    observed_fields["model"] = "old-model"

    runtime = FakeRuntimeAdapter(project_id="demo", fingerprint=observed_fields)
    ledger = LedgerStore(tmp_path / "ledger.db")
    reconciler = Reconciler(contract=contract, runtime=runtime, ledger=ledger)

    result = reconciler.reconcile(desired_fingerprint=desired_fields)

    assert result.success is False
    assert result.blocked_reason is not None
    assert "runtime_restarts" in result.blocked_reason
    assert runtime.relaunches == []


def test_reconcile_noop_when_current(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    fields = {
        "git_sha": "abc",
        "lock_hash": "lock",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "prompt_version": "v1",
        "dataset_version": "d1",
        "evaluator_version": "e1",
        "config_hash": "cfg",
    }
    runtime = FakeRuntimeAdapter(project_id="demo", fingerprint=fields)
    ledger = LedgerStore(tmp_path / "ledger.db")
    reconciler = Reconciler(contract=contract, runtime=runtime, ledger=ledger)

    result = reconciler.reconcile(desired_fingerprint=fields)

    assert result.success is True
    assert result.differences == []
    assert result.actions_taken == []
    assert runtime.relaunches == []


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
        "authorization_sha256": "auth-old",
    }
    base.update(overrides)
    return base


class SyncableRuntime(FakeRuntimeAdapter):
    def __init__(
        self,
        *,
        project_id: str,
        running: dict[str, str],
        repository: dict[str, str],
        repairable_actions: set[str] | None = None,
    ) -> None:
        super().__init__(project_id=project_id, fingerprint=running)
        self._repository = dict(repository)
        self.repairable_actions = repairable_actions or set()
        self.authorization_ok = False
        self.repairs_applied: list[str] = []

    def repository_fingerprint(self) -> dict[str, str]:
        return dict(self._repository)

    def fingerprint_field_classifications(self) -> dict[str, str]:
        return {
            "config_hash": FingerprintFieldClass.DEPLOYMENT.value,
            "authorization_sha256": FingerprintFieldClass.AUTHORIZATION_SENSITIVE.value,
            "prompt_version": FingerprintFieldClass.RESEARCH_SEMANTIC.value,
        }

    def mutation_preflight(self, action: str):
        from research_harness.models.mutation import MutationReadiness, MutationRepair

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

    def repair_mutation_prerequisite(self, repair_id: str):
        from research_harness.models.mutation import MutationRepairResult

        if repair_id != "refresh_scheduler_authorization":
            return MutationRepairResult.failed(repair_id, detail="unknown repair")
        self.authorization_ok = True
        self.repairs_applied.append(repair_id)
        self._repository["authorization_sha256"] = "auth-new"
        return MutationRepairResult.ok(repair_id)


def test_merge_repository_deployment_fields_skips_research_semantic() -> None:
    runtime = SyncableRuntime(
        project_id="demo",
        running=_fields(),
        repository=_fields(
            config_hash="cfg-b",
            authorization_sha256="auth-new",
            prompt_version="v9",
        ),
    )
    desired = _fields(config_hash="cfg-a", authorization_sha256="auth-old", prompt_version="v2")

    merged, synced = merge_repository_deployment_fields(runtime, desired)

    assert merged["config_hash"] == "cfg-b"
    assert merged["authorization_sha256"] == "auth-new"
    assert merged["prompt_version"] == "v2"
    assert set(synced) == {"config_hash", "authorization_sha256"}


def test_reconcile_syncs_desired_after_repair(tmp_path: Any) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    desired = _fields(prompt_version="v2", config_hash="cfg-b", authorization_sha256="auth-old")
    observed = _fields(prompt_version="v1", config_hash="cfg-a", authorization_sha256="auth-old")
    repository = _fields(prompt_version="v2", config_hash="cfg-b", authorization_sha256="auth-old")
    runtime = SyncableRuntime(
        project_id="demo",
        running=observed,
        repository=repository,
        repairable_actions={"worker_restart"},
    )
    runtime._pending_fingerprint = desired  # noqa: SLF001
    desired_path = tmp_path / "desired_fingerprint.json"
    write_fingerprint_file(desired_path, desired)
    ledger = LedgerStore(tmp_path / "ledger.db")
    reconciler = Reconciler(
        contract=contract,
        runtime=runtime,
        ledger=ledger,
        persist_desired_path=desired_path,
    )

    result = reconciler.reconcile(desired_fingerprint=desired)

    assert result.success is True
    assert "repair:refresh_scheduler_authorization" in result.actions_taken
    assert "sync_desired:authorization_sha256" in result.actions_taken
    persisted = load_fingerprint_file(desired_path)
    assert persisted["authorization_sha256"] == "auth-new"
    assert persisted["prompt_version"] == "v2"
