from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_harness.adapters.failing_worker import FailingWorkerRuntime, WorkerConfig
from research_harness.contract.template import default_contract
from research_harness.incidents import IncidentEngine, IncidentStore
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import Health, Progress, RuntimeFreshness
from research_harness.models.state import ObservedState
from research_harness.results import ScientificResultRecorder
from research_harness.validity.evaluator import ValidityResult
from research_harness.watchdog.evaluator import WatchdogResult


def test_failing_worker_persists_state(tmp_path: Path) -> None:
    runtime = FailingWorkerRuntime(project_id="demo", state_dir=tmp_path)
    runtime.store.save_config(WorkerConfig(crash_at_units=[], stall_at_units=[], total_units=20))
    runtime.process_units(count=5)
    reloaded = FailingWorkerRuntime(project_id="demo", state_dir=tmp_path)
    observed = reloaded.inspect()
    assert observed.completed_units == 5
    assert observed.health.value == "HEALTHY"


def test_failing_worker_crash_and_resume(tmp_path: Path) -> None:
    runtime = FailingWorkerRuntime(project_id="demo", state_dir=tmp_path)
    runtime.store.save_config(
        WorkerConfig(crash_at_units=[3], stall_at_units=[], total_units=20)
    )
    runtime.process_units(count=5)
    observed = runtime.inspect()
    assert observed.completed_units == 3
    assert observed.health.value == "UNHEALTHY"

    runtime.restart_worker()
    runtime.process_units(count=2)
    observed = runtime.inspect()
    assert observed.completed_units == 5
    assert observed.health.value == "HEALTHY"


def test_failing_worker_config_swap_is_stale_until_relaunch(tmp_path: Path) -> None:
    runtime = FailingWorkerRuntime(project_id="demo", state_dir=tmp_path)
    runtime.set_pending_config("cfg-b")
    observed = runtime.inspect()
    assert observed.runtime_freshness.value == "STALE"
    assert observed.fingerprint["config_hash"] == "cfg-b"

    runtime.relaunch("worker_restart")
    observed = runtime.inspect()
    assert observed.runtime_freshness.value == "CURRENT"
    assert observed.fingerprint["config_hash"] == "cfg-b"


def test_scientific_result_recorder_writes_ledger(tmp_path: Path) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    ledger = LedgerStore(tmp_path / "ledger.db")
    recorder = ScientificResultRecorder(
        project_id="demo",
        contract_version=contract.contract_version,
        ledger=ledger,
        effect_threshold=0.05,
    )
    observed = ObservedState(
        project_id="demo",
        observed_at=datetime.now(UTC),
        health=Health.HEALTHY,
        progress=Progress.ADVANCING,
        runtime_freshness=RuntimeFreshness.CURRENT,
        completed_units=80,
    )
    watchdog = WatchdogResult(
        health=Health.HEALTHY,
        progress=Progress.ADVANCING,
        stalled_watermarks=(),
        stalled_phases=(),
        symptom=None,
        should_open_incident=False,
    )
    validity = ValidityResult(passed=True)

    record = recorder.maybe_record(
        observed=observed,
        watchdog=watchdog,
        validity=validity,
        metric_value=0.01,
    )
    assert record.recorded
    assert record.outcome is not None
    assert not record.outcome.hypothesis_supported

    events = ledger.list_events(project_id="demo")
    assert any(event.event_type == LedgerEventType.SCIENTIFIC_RESULT for event in events)


@pytest.mark.acceptance
def test_scenario_f_scientific_negative_result(tmp_path: Path) -> None:
    """F: metric contradicts hypothesis but ops OK → recorded, no incident."""
    contract = default_contract(project_id="failing-worker", objective="Negative result test")
    contract.authority.runtime_restarts = True
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=tmp_path)
    runtime.store.save_config(
        WorkerConfig(
            crash_at_units=[],
            stall_at_units=[],
            total_units=100,
            seed=7,
        )
    )
    runtime.process_units(count=60)

    ledger = LedgerStore(tmp_path / "ledger.db")
    incidents = IncidentStore(tmp_path / "incidents.db")
    engine = IncidentEngine(
        contract=contract,
        runtime=runtime,
        checkpoint=runtime,
        incident_store=incidents,
        ledger=ledger,
    )
    desired_fp = runtime.store.load_config().fingerprint()
    observed = runtime.inspect()
    result = engine.evaluate(
        observed=observed,
        progress=runtime.progress_context(),
        desired_fingerprint=desired_fp,
    )
    assert result.incident is None
    assert not result.watchdog.should_open_incident

    recorder = ScientificResultRecorder(
        project_id=contract.project.id,
        contract_version=contract.contract_version,
        ledger=ledger,
        effect_threshold=0.05,
    )
    record = recorder.maybe_record(
        observed=observed,
        watchdog=result.watchdog,
        validity=result.validity,
        metric_value=0.01,
    )
    assert record.recorded
    assert record.outcome is not None
    assert record.outcome.interpretation == "hypothesis_not_supported_negative_result"
    assert incidents.list_open(project_id=contract.project.id) == []
    assert any(
        event.event_type == LedgerEventType.SCIENTIFIC_RESULT
        for event in ledger.list_events(project_id=contract.project.id)
    )
