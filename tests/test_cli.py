from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from research_harness.cli import app
from research_harness.runtime.io import write_fingerprint_file

runner = CliRunner()


def test_init_creates_contract(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    result = runner.invoke(
        app,
        [
            "init",
            "--id",
            "demo",
            "--objective",
            "Test objective.",
            "--contract",
            str(contract_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert contract_path.exists()


def test_init_with_experiment_scaffolds_plan(tmp_path: Path) -> None:
    from research_harness.contract.loader import load_contract
    from research_harness.experiment.plan import compute_plan_hash, load_experiment_plan

    contract_path = tmp_path / "contract.yaml"
    result = runner.invoke(
        app,
        [
            "init",
            "--id",
            "demo",
            "--objective",
            "Test objective.",
            "--contract",
            str(contract_path),
            "--with-experiment",
            "--planned-units",
            "4",
        ],
    )
    assert result.exit_code == 0, result.output
    contract = load_contract(contract_path)
    assert contract.experiment is not None
    assert contract.validity.expected_units == 4
    plan = load_experiment_plan(tmp_path / "experiment" / "plan.json")
    assert plan.planned_units == 4
    assert plan.frozen_before_outcomes is True
    assert plan.plan_hash == compute_plan_hash(plan)
    assert (tmp_path / "experiment" / "schedule.csv").exists()


def test_validate_accepts_fixture_contract() -> None:
    fixture = Path(__file__).parent / "fixtures" / "contract-v1.1.yaml"
    result = runner.invoke(app, ["validate", "--contract", str(fixture)])
    assert result.exit_code == 0, result.output
    assert "Contract valid" in result.output


def test_status_without_ledger(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    runner.invoke(
        app,
        [
            "init",
            "--id",
            "demo",
            "--objective",
            "Test objective.",
            "--contract",
            str(contract_path),
        ],
    )
    result = runner.invoke(app, ["status", "--contract", str(contract_path)])
    assert result.exit_code == 0, result.output
    assert "Project: demo" in result.output
    assert "Lifecycle: STOPPED" in result.output


def test_reconcile_cli_detects_and_fixes_stale(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    state_dir = tmp_path / "state"
    runner.invoke(
        app,
        [
            "init",
            "--id",
            "demo",
            "--objective",
            "Test objective.",
            "--contract",
            str(contract_path),
        ],
    )
    fields = {
        "git_sha": "abc",
        "lock_hash": "lock",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "prompt_version": "v2",
        "dataset_version": "d1",
        "evaluator_version": "e1",
        "config_hash": "cfg",
    }
    write_fingerprint_file(state_dir / "desired_fingerprint.json", fields)
    stale = dict(fields)
    stale["prompt_version"] = "v1"
    write_fingerprint_file(state_dir / "observed_fingerprint.json", stale)

    result = runner.invoke(
        app,
        [
            "reconcile",
            "--contract",
            str(contract_path),
            "--state-dir",
            str(state_dir),
            "--ledger",
            str(state_dir / "ledger.db"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Reconciled stale runtime" in result.output
    assert "worker_restart" in result.output

    status = runner.invoke(
        app,
        [
            "status",
            "--contract",
            str(contract_path),
            "--state-dir",
            str(state_dir),
            "--ledger",
            str(state_dir / "ledger.db"),
        ],
    )
    assert status.exit_code == 0, status.output
    assert "Runtime freshness: CURRENT" in status.output
