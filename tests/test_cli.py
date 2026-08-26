from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from research_harness.cli import app

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
    assert "State: STOPPED" in result.output
