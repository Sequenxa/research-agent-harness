from __future__ import annotations

from pathlib import Path

import pytest

from research_harness.adapters.fake_worker import FakeWorker
from research_harness.contract.models import Duration
from research_harness.contract.template import default_contract
from research_harness.incidents import IncidentEngine, IncidentRecordStatus, IncidentStore
from research_harness.ledger import LedgerStore


def _setup(tmp_path: Path) -> tuple:
    contract = default_contract(project_id="demo", objective="Test crash recovery.")
    contract.authority.runtime_restarts = True
    for watermark in contract.progress.watermarks:
        watermark.stall_after = Duration.parse("1s")

    worker = FakeWorker(project_id="demo", fingerprint={"model": "test"})
    ledger = LedgerStore(tmp_path / "ledger.db")
    incidents = IncidentStore(tmp_path / "incidents.db")
    engine = IncidentEngine(
        contract=contract,
        runtime=worker,
        checkpoint=worker,
        incident_store=incidents,
        ledger=ledger,
    )
    desired_fp = {"model": "test"}
    return contract, worker, engine, incidents, desired_fp


@pytest.mark.acceptance
def test_scenario_a_crash_recovery(tmp_path: Path) -> None:
    """A: crash → detect → incident → restart → checkpoint resume → progress → close."""
    _, worker, engine, incidents, desired_fp = _setup(tmp_path)

    worker.tick(units=5)
    worker.save_checkpoint({"completed_units": worker.completed_units})
    worker.crash()

    observed = worker.inspect()
    result = engine.evaluate(
        observed=observed,
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    assert result.incident is not None
    assert result.incident.symptom == "worker_unhealthy"
    assert "worker_restart" in result.actions_taken
    assert worker.restart_count == 1
    assert worker.completed_units == 5

    worker.tick(units=2)
    observed = worker.inspect()
    result2 = engine.evaluate(
        observed=observed,
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    closed = incidents.get(result.incident.incident_id)
    assert closed is not None
    assert closed.status == IncidentRecordStatus.CLOSED
    assert "incident_closed" in result2.actions_taken
    assert worker.completed_units == 7


@pytest.mark.acceptance
def test_scenario_b_alive_but_stalled(tmp_path: Path) -> None:
    """B: alive but stalled → STALLED → recovery → progress resumes."""
    _, worker, engine, incidents, desired_fp = _setup(tmp_path)

    worker.tick(units=3)
    worker.stall()

    observed = worker.inspect()
    result = engine.evaluate(
        observed=observed,
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    assert result.watchdog.health.value == "HEALTHY"
    assert result.watchdog.progress.value == "STALLED"
    assert result.incident is not None
    assert result.incident.symptom == "progress_stalled"
    assert "worker_restart" in result.actions_taken

    worker.tick(units=1)
    observed = worker.inspect()
    result2 = engine.evaluate(
        observed=observed,
        progress=worker.progress_context(),
        desired_fingerprint=desired_fp,
    )
    closed = incidents.get(result.incident.incident_id)
    assert closed is not None
    assert closed.status == IncidentRecordStatus.CLOSED
    assert "incident_closed" in result2.actions_taken
