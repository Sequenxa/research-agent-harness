from __future__ import annotations

import multiprocessing as mp
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research_harness.adapters.fake_worker import FakeWorker
from research_harness.budget import BudgetTracker
from research_harness.contract.models import Duration, ValidityCheck
from research_harness.contract.template import default_contract
from research_harness.incidents import IncidentEngine, IncidentStore
from research_harness.ledger import LedgerStore
from research_harness.models.enums import RuntimeFreshness
from research_harness.recovery import IntentStore
from research_harness.reconciliation import Reconciler
from research_harness.supervisor import ProjectLease, Supervisor, request_stop


def _full_fingerprint(model: str) -> dict[str, str]:
    return {
        "git_sha": "abc",
        "lock_hash": "lock",
        "model": model,
        "provider": "openai",
        "prompt_version": "v1",
        "dataset_version": "d1",
        "evaluator_version": "e1",
        "config_hash": "cfg",
    }


def _fast_contract():
    contract = default_contract(project_id="demo", objective="obj")
    contract.authority.runtime_restarts = True
    for watermark in contract.progress.watermarks:
        watermark.stall_after = Duration.parse("1s")
    contract.verification.stable_after.units = 2
    contract.verification.stable_after.min_duration = Duration.parse("0s")
    return contract


def _lease_worker(state_dir: str, instance_id: str, hold_seconds: float, result_queue: object) -> None:
    lease = ProjectLease(state_dir=Path(state_dir), project_id="demo", ttl_seconds=1.0)
    acquired = lease.acquire(instance_id)
    result_queue.put(acquired)  # type: ignore[attr-defined]
    if acquired:
        deadline = time.time() + hold_seconds
        while time.time() < deadline:
            lease.renew(instance_id)
            time.sleep(0.2)
        lease.release(instance_id)


def test_lease_exclusive_across_processes(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    queue_a: mp.Queue[bool] = ctx.Queue()
    queue_b: mp.Queue[bool] = ctx.Queue()
    state_dir = str(tmp_path)

    process_a = ctx.Process(
        target=_lease_worker,
        args=(state_dir, "instance-a", 2.0, queue_a),
    )
    process_a.start()
    assert queue_a.get(timeout=5) is True
    time.sleep(0.3)

    process_b = ctx.Process(
        target=_lease_worker,
        args=(state_dir, "instance-b", 0.5, queue_b),
    )
    process_b.start()
    assert queue_b.get(timeout=5) is False

    process_a.join(timeout=5)
    process_b.join(timeout=5)

    lease = ProjectLease(state_dir=tmp_path, project_id="demo", ttl_seconds=1.0)
    assert lease.acquire("instance-c")


def test_supervisor_observe_only_never_mutates_runtime(tmp_path: Path) -> None:
    contract = _fast_contract()
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    worker.crash()
    supervisor = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=LedgerStore(tmp_path / "ledger.db"),
        observe_only=True,
    )
    supervisor.startup()
    before_restarts = worker.restart_count
    result = supervisor.tick()
    supervisor.shutdown()

    assert worker.restart_count == before_restarts
    assert not worker.running
    assert any(action.startswith("would_") for action in result.actions) or result.blocked


def test_supervisor_scenario_e_blocked_persists(tmp_path: Path) -> None:
    contract = _fast_contract()
    contract.authority.runtime_restarts = False
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    supervisor = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    worker.crash()
    supervisor.startup()
    first = supervisor.tick()
    second = supervisor.tick()
    supervisor.shutdown()

    assert first.blocked
    assert second.blocked
    assert worker.restart_count == 0
    assert "authority.runtime_restarts is false" in (first.message or "")


def test_supervisor_reconcile_blocked_stops_tick(tmp_path: Path) -> None:
    contract = _fast_contract()
    contract.authority.runtime_restarts = False
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("model-a"))
    desired = _full_fingerprint("model-b")
    worker.set_pending_fingerprint(desired)
    supervisor = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    supervisor.startup()
    result = supervisor.tick()
    supervisor.shutdown()

    assert result.blocked
    assert worker.fingerprint["model"] == "model-a"
    assert "authority.runtime_restarts is false" in (result.message or "")


def test_supervisor_validity_block(tmp_path: Path) -> None:
    contract = _fast_contract()
    contract.validity.checks = [
        ValidityCheck(id="custom_gate", adapter="demo", on_fail="block")
    ]

    class GatedWorker(FakeWorker):
        def custom_validity_results(self) -> dict[str, bool]:
            return {"custom_gate": False}

    worker = GatedWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    supervisor = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    supervisor.startup()
    result = supervisor.tick()
    supervisor.shutdown()

    assert result.blocked
    assert "validity blocked" in (result.message or "")


def test_supervisor_completion_stops_workers(tmp_path: Path) -> None:
    contract = _fast_contract()
    contract.validity.expected_units = 3
    contract.completion.condition = "units_completed >= 3"
    contract.completion.on_complete = ["snapshot_ledger", "stop_workers"]
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    worker.tick(units=3)
    supervisor = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    supervisor.startup()
    result = supervisor.tick()
    supervisor.shutdown()

    assert result.completed
    assert not worker.running
    assert "stop_workers" in result.actions


def test_zero_budget_blocks_before_reconcile_action(tmp_path: Path) -> None:
    contract = _fast_contract()
    contract.budget.total_usd = 0.0
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("model-a"))
    desired = _full_fingerprint("model-b")
    worker.set_pending_fingerprint(desired)
    reconciler = Reconciler(
        contract=contract,
        runtime=worker,
        ledger=LedgerStore(tmp_path / "ledger.db"),
        budget_tracker=BudgetTracker(state_dir=tmp_path, contract=contract),
    )
    result = reconciler.reconcile(desired_fingerprint=desired)
    assert not result.success
    assert result.blocked_reason is not None
    assert "budget.total_usd exceeded" in result.blocked_reason
    assert worker.fingerprint["model"] == "model-a"


def test_orphan_intent_failure_not_marked_executed(tmp_path: Path) -> None:
    contract = _fast_contract()
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    worker.failed_strategies = {"worker_restart"}
    incidents = IncidentStore(tmp_path / "incidents.db")
    intents = IntentStore(tmp_path / "intents.db")
    engine = IncidentEngine(
        contract=contract,
        runtime=worker,
        checkpoint=worker,
        incident_store=incidents,
        intent_store=intents,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    incident = incidents.create(
        project_id="demo",
        contract_version=1,
        symptom="worker_unhealthy",
        evidence={},
    )
    intent = intents.create_pending(
        project_id="demo",
        incident_id=incident.incident_id,
        strategy="worker_restart",
        evidence={},
    )
    worker.crash()
    actions = engine.reconcile_orphaned_intents()
    assert any(action.startswith("orphan_failed:") for action in actions)
    refreshed = intents.list_orphaned_pending(project_id="demo")
    assert all(item.intent_id != intent.intent_id for item in refreshed)


def test_budget_persists_across_supervisor_restarts(tmp_path: Path) -> None:
    contract = _fast_contract()
    contract.budget.total_usd = 1.0
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("model-a"))
    desired = _full_fingerprint("model-b")
    worker.set_pending_fingerprint(desired)
    ledger = LedgerStore(tmp_path / "ledger.db")

    supervisor_a = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=ledger,
        instance_id="instance-a",
    )
    supervisor_a.startup()
    supervisor_a.budget.record_spend(0.5)
    supervisor_a.shutdown()

    supervisor_b = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=ledger,
        instance_id="instance-b",
    )
    assert supervisor_b.budget.state.spend_usd == 0.5


def test_escalation_timeout_stops_after_blocked(tmp_path: Path) -> None:
    contract = _fast_contract()
    contract.authority.runtime_restarts = False
    contract.escalation.blocking_timeout = Duration.parse("1s")
    contract.escalation.on_timeout = "stop"
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("model-a"))
    desired = _full_fingerprint("model-b")
    worker.set_pending_fingerprint(desired)
    supervisor = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    supervisor.startup()
    first = supervisor.tick()
    assert first.blocked
    assert worker.running

    time.sleep(1.1)
    second = supervisor.tick()
    supervisor.shutdown()

    assert second.stopped
    assert not worker.running
    assert "escalation timeout" in (second.message or "")


def test_stop_without_verified_stop_returns_blocked(tmp_path: Path) -> None:
    contract = _fast_contract()

    class UnstoppableWorker(FakeWorker):
        def stop(self) -> None:
            return None

    worker = UnstoppableWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    supervisor = Supervisor(
        contract=contract,
        runtime=worker,
        state_dir=tmp_path,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    supervisor.startup()
    request_stop(tmp_path)
    result = supervisor.tick()
    supervisor.shutdown()

    assert result.blocked
    assert worker.running
    assert "could not be stopped" in (result.message or "")


def test_lease_stale_after_ttl_without_renewal(tmp_path: Path) -> None:
    lease = ProjectLease(state_dir=tmp_path, project_id="demo", ttl_seconds=0.2)
    assert lease.acquire("instance-a")
    time.sleep(0.3)
    info = lease.read()
    assert info is not None
    assert info.expires_at <= datetime.now(UTC) + timedelta(seconds=0.05)
    assert lease.acquire("instance-b")
