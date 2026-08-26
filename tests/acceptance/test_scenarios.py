from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_harness.adapters.fake_worker import FakeWorker
from research_harness.contract.models import Duration
from research_harness.contract.template import default_contract
from research_harness.incidents import IncidentEngine, IncidentRecordStatus, IncidentStore
from research_harness.ledger import LedgerStore
from research_harness.reconciliation import Reconciler
from research_harness.recovery import IntentStore, RecoveryBudgetTracker
from research_harness.supervisor import ProjectLease


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


def _setup_engine(tmp_path: Path) -> tuple:
    contract = _fast_contract()
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    ledger = LedgerStore(tmp_path / "ledger.db")
    incidents = IncidentStore(tmp_path / "incidents.db")
    intents = IntentStore(tmp_path / "intents.db")
    engine = IncidentEngine(
        contract=contract,
        runtime=worker,
        checkpoint=worker,
        incident_store=incidents,
        intent_store=intents,
        ledger=ledger,
    )
    return contract, worker, engine, incidents


def _reach_stable(engine: IncidentEngine, worker: FakeWorker, desired_fp: dict[str, str]) -> None:
    worker.tick(units=2)
    engine.evaluate(
        observed=worker.inspect(),
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )


@pytest.mark.acceptance
def test_scenario_a_crash_recovery(tmp_path: Path) -> None:
    """A: crash → detect → incident → restart → checkpoint resume → stable close."""
    _, worker, engine, incidents = _setup_engine(tmp_path)
    desired_fp = _full_fingerprint("test")

    worker.tick(units=5)
    worker.save_checkpoint({"completed_units": worker.completed_units})
    worker.crash()

    result = engine.evaluate(
        observed=worker.inspect(),
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    assert result.incident is not None
    assert result.incident.symptom == "worker_unhealthy"
    assert "worker_restart" in result.actions_taken
    assert worker.completed_units == 5

    _reach_stable(engine, worker, desired_fp)
    closed = incidents.get(result.incident.incident_id)
    assert closed is not None
    assert closed.status == IncidentRecordStatus.CLOSED
    assert closed.resolution == "stable_after_burn_in"


@pytest.mark.acceptance
def test_scenario_b_alive_but_stalled(tmp_path: Path) -> None:
    """B: alive but stalled → STALLED → recovery → stable close."""
    _, worker, engine, incidents = _setup_engine(tmp_path)
    desired_fp = _full_fingerprint("test")

    worker.tick(units=3)
    worker.stall()

    result = engine.evaluate(
        observed=worker.inspect(),
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    assert result.watchdog.progress.value == "STALLED"
    assert result.incident is not None

    _reach_stable(engine, worker, desired_fp)
    closed = incidents.get(result.incident.incident_id)
    assert closed is not None
    assert closed.status == IncidentRecordStatus.CLOSED


@pytest.mark.acceptance
def test_scenario_c_runtime_swap(tmp_path: Path) -> None:
    """C: config A→B → fingerprint stale → relaunch → progress under B."""
    contract = _fast_contract()
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("model-a"))
    ledger = LedgerStore(tmp_path / "ledger.db")
    reconciler = Reconciler(contract=contract, runtime=worker, ledger=ledger)

    desired = _full_fingerprint("model-b")
    worker.set_pending_fingerprint(desired)
    result = reconciler.reconcile(desired_fingerprint=desired)

    assert result.success
    assert "worker_restart" in result.actions_taken or "full_relaunch" in result.actions_taken
    assert worker.fingerprint["model"] == "model-b"

    worker.tick(units=4)
    observed = worker.inspect()
    assert observed.fingerprint["model"] == "model-b"
    assert observed.completed_units == 4


@pytest.mark.acceptance
def test_scenario_d_bad_fix_switches_strategy(tmp_path: Path) -> None:
    """D: strategy A fails twice → different strategy → evidence records attempts."""
    contract = _fast_contract()
    contract.recovery.max_identical_attempts = 2
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    worker.failed_strategies = {"worker_restart"}
    ledger = LedgerStore(tmp_path / "ledger.db")
    incidents = IncidentStore(tmp_path / "incidents.db")
    intents = IntentStore(tmp_path / "intents.db")
    engine = IncidentEngine(
        contract=contract,
        runtime=worker,
        checkpoint=worker,
        incident_store=incidents,
        intent_store=intents,
        ledger=ledger,
    )
    desired_fp = _full_fingerprint("test")

    worker.crash()
    engine.evaluate(
        observed=worker.inspect(),
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    worker.crash()
    engine.evaluate(
        observed=worker.inspect(),
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    worker.crash()
    result = engine.evaluate(
        observed=worker.inspect(),
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )

    assert "service_restart" in worker.recovery_log
    open_incident = incidents.list_open(project_id="demo")[0]
    attempts = open_incident.evidence["recovery_attempts"]
    strategies = [item["strategy"] for item in attempts]
    assert strategies.count("worker_restart") == 2
    assert "service_restart" in strategies
    assert result.incident is not None


@pytest.mark.acceptance
def test_scenario_e_unauthorized_boundary(tmp_path: Path) -> None:
    """E: recovery prohibited by contract → BLOCKED with boundary explanation."""
    contract = _fast_contract()
    contract.authority.runtime_restarts = False
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
    incidents = IncidentStore(tmp_path / "incidents.db")
    engine = IncidentEngine(
        contract=contract,
        runtime=worker,
        checkpoint=worker,
        incident_store=incidents,
        ledger=LedgerStore(tmp_path / "ledger.db"),
    )
    desired_fp = _full_fingerprint("test")

    worker.crash()
    result = engine.evaluate(
        observed=worker.inspect(),
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )

    assert result.lifecycle.value == "BLOCKED"
    assert result.blocked_reason is not None
    assert "authority.runtime_restarts is false" in result.blocked_reason
    assert worker.restart_count == 0


def test_recovery_budget_oscillation_blocks() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    tracker = RecoveryBudgetTracker(contract=contract)
    now = datetime.now(UTC)
    evidence: dict[str, object] = {"symptom": "stall"}
    for strategy in ("worker_restart", "service_restart", "worker_restart", "service_restart"):
        tracker.record_attempt(strategy=strategy, evidence=evidence, now=now, succeeded=False)
    decision = tracker.next_strategy(
        evidence=evidence,
        incident_opened_at=now - timedelta(minutes=1),
        now=now,
    )
    assert not decision.allowed
    assert decision.blocked_reason == "oscillation detected"


def test_project_lease_exclusive(tmp_path: Path) -> None:
    lease_a = ProjectLease(state_dir=tmp_path, project_id="demo", ttl_seconds=60)
    lease_b = ProjectLease(state_dir=tmp_path, project_id="demo", ttl_seconds=60)
    assert lease_a.acquire("supervisor-a")
    assert not lease_b.acquire("supervisor-b")
    lease_a.release("supervisor-a")
    assert lease_b.acquire("supervisor-b")


def test_orphan_intent_reconciled(tmp_path: Path) -> None:
    contract = _fast_contract()
    worker = FakeWorker(project_id="demo", fingerprint=_full_fingerprint("test"))
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
    intents.create_pending(
        project_id="demo",
        incident_id=incident.incident_id,
        strategy="worker_restart",
        evidence={},
    )
    worker.crash()
    actions = engine.reconcile_orphaned_intents()
    assert any(action.startswith("orphan_reconciled:") for action in actions)
    assert worker.running
