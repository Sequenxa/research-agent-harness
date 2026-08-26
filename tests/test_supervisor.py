from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from research_harness.adapters.failing_worker import FailingWorkerRuntime, WorkerConfig
from research_harness.cli import app
from research_harness.contract.loader import load_contract, write_contract
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.supervisor import Supervisor, request_stop, stop_requested
from research_harness.supervisor.stop import clear_stop

runner = CliRunner()


def _init_failing_worker(tmp_path: Path) -> tuple[Path, Path]:
    contract_path = tmp_path / "contract.yaml"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    contract = default_contract(
        project_id="failing-worker",
        objective="Supervisor test workload.",
    )
    contract.authority.runtime_restarts = True
    write_contract(contract, contract_path)
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=state_dir)
    runtime.store.save_config(WorkerConfig(total_units=10, crash_at_units=[], stall_at_units=[]))
    return contract_path, state_dir


def test_supervisor_tick_records_ledger(tmp_path: Path) -> None:
    contract_path, state_dir = _init_failing_worker(tmp_path)
    contract = load_contract(contract_path)
    ledger_path = state_dir / "ledger.db"
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=state_dir)
    runtime.process_units(1)

    supervisor = Supervisor(
        contract=contract,
        runtime=runtime,
        state_dir=state_dir,
        ledger=LedgerStore(ledger_path),
        runtime_kind="failing-worker",
    )
    supervisor.startup()
    result = supervisor.tick()
    supervisor.shutdown()

    assert result.lifecycle.value == "RUNNING"
    store = LedgerStore(ledger_path)
    events = store.list_events(project_id="failing-worker")
    assert any(event.event_type == LedgerEventType.EXPERIMENT_START for event in events)
    assert any(event.event_type == LedgerEventType.EXPERIMENT_STOP for event in events)


def test_stop_flag_stops_tick(tmp_path: Path) -> None:
    contract_path, state_dir = _init_failing_worker(tmp_path)
    contract = load_contract(contract_path)
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=state_dir)
    request_stop(state_dir)
    supervisor = Supervisor(
        contract=contract,
        runtime=runtime,
        state_dir=state_dir,
        ledger=LedgerStore(state_dir / "ledger.db"),
        runtime_kind="failing-worker",
    )
    result = supervisor.tick()
    clear_stop(state_dir)
    assert result.stopped is True
    assert stop_requested(state_dir) is False


def test_cli_run_max_ticks(tmp_path: Path) -> None:
    contract_path, state_dir = _init_failing_worker(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            "--contract",
            str(contract_path),
            "--state-dir",
            str(state_dir),
            "--ledger",
            str(state_dir / "ledger.db"),
            "--runtime",
            "failing-worker",
            "--max-ticks",
            "2",
            "--interval",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Supervisor stopped" in result.output


def test_cli_doctor_and_stop(tmp_path: Path) -> None:
    contract_path, state_dir = _init_failing_worker(tmp_path)
    doctor = runner.invoke(
        app,
        [
            "doctor",
            "--contract",
            str(contract_path),
            "--state-dir",
            str(state_dir),
            "--ledger",
            str(state_dir / "ledger.db"),
            "--runtime",
            "failing-worker",
        ],
    )
    assert doctor.exit_code == 0, doctor.output
    assert "Doctor: ok" in doctor.output

    stop = runner.invoke(app, ["stop", "--state-dir", str(state_dir)])
    assert stop.exit_code == 0, stop.output
    assert stop_requested(state_dir)


def test_failing_worker_diagnostics_collect(tmp_path: Path) -> None:
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=tmp_path)
    runtime.process_units(3)
    diagnostics = runtime.collect(symptom="worker_stalled")
    assert diagnostics["symptom"] == "worker_stalled"
    assert diagnostics["completed_units"] == 3
