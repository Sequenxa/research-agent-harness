from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import Health, Lifecycle, Progress, RuntimeFreshness
from research_harness.models.state import ObservedState
from research_harness.reconciliation.engine import Reconciler
from research_harness.runtime.desired import build_desired_state


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
