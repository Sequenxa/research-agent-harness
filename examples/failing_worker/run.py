#!/usr/bin/env python3
"""Run the deterministic failing_worker example workload."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from research_harness.adapters.failing_worker import FailingWorkerRuntime, WorkerConfig
from research_harness.contract.loader import load_contract, write_contract
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerStore
from research_harness.reconciliation import Reconciler

app = typer.Typer(help="Deterministic failing_worker research workload.")

DEFAULT_STATE_DIR = Path("examples/failing_worker/.state")
DEFAULT_CONTRACT = Path("examples/failing_worker/contract.yaml")


def _runtime(state_dir: Path) -> FailingWorkerRuntime:
    return FailingWorkerRuntime(project_id="failing-worker", state_dir=state_dir)


def _desired_fingerprint(runtime: FailingWorkerRuntime) -> dict[str, str]:
    config = runtime.store.load_config()
    state = runtime.store.load_state()
    desired = config.fingerprint()
    if state.pending_config_hash is not None:
        desired["config_hash"] = state.pending_config_hash
    return desired


@app.command()
def init(
    state_dir: Path = DEFAULT_STATE_DIR,
    contract: Path = DEFAULT_CONTRACT,
) -> None:
    """Initialize worker state and example contract."""
    state_dir.mkdir(parents=True, exist_ok=True)
    runtime = _runtime(state_dir)
    runtime.store.save_config(WorkerConfig())
    runtime.store.save_state(runtime.store.load_state())

    starter = default_contract(
        project_id="failing-worker",
        objective="Demonstrate harness recovery against a deterministic worker.",
    )
    starter.authority.runtime_restarts = True
    write_contract(starter, contract)
    typer.echo(f"Initialized worker state in {state_dir}")
    typer.echo(f"Wrote contract to {contract}")


@app.command()
def step(
    count: int = typer.Option(1, help="Units to process."),
    state_dir: Path = DEFAULT_STATE_DIR,
) -> None:
    """Process experiment units."""
    runtime = _runtime(state_dir)
    state = runtime.process_units(count=count)
    effect = runtime.current_effect_size()
    typer.echo(
        f"units={state.completed_units} running={state.running} effect_size={effect:.4f}"
    )


@app.command("swap-config")
def swap_config(
    config_hash: str = typer.Option("cfg-b", help="Pending config hash requiring relaunch."),
    state_dir: Path = DEFAULT_STATE_DIR,
) -> None:
    """Stage a config swap that requires harness relaunch to apply."""
    runtime = _runtime(state_dir)
    runtime.set_pending_config(config_hash)
    typer.echo(f"Pending config_hash={config_hash} (harness reconcile required)")


@app.command()
def reconcile(
    state_dir: Path = DEFAULT_STATE_DIR,
    contract: Path = DEFAULT_CONTRACT,
) -> None:
    """Reconcile desired vs observed fingerprint using FailingWorkerRuntime."""
    if not contract.exists():
        typer.echo(f"Contract not found: {contract}. Run init first.", err=True)
        raise typer.Exit(code=1)

    loaded = load_contract(contract)
    runtime = _runtime(state_dir)
    desired_fp = _desired_fingerprint(runtime)
    ledger = LedgerStore(state_dir / "ledger.db")
    reconciler = Reconciler(contract=loaded, runtime=runtime, ledger=ledger)
    result = reconciler.reconcile(desired_fingerprint=desired_fp)

    observed = runtime.inspect()
    if result.success and not result.differences:
        typer.echo("Runtime current — no reconciliation needed.")
    elif result.success:
        typer.echo("Reconciled stale runtime.")
        typer.echo(f"Actions: {', '.join(result.actions_taken)}")
        typer.echo(f"Observed config_hash: {observed.fingerprint.get('config_hash')}")
    else:
        typer.echo(f"Reconciliation failed: {result.blocked_reason}", err=True)
        raise typer.Exit(code=1)


@app.command()
def status(state_dir: Path = DEFAULT_STATE_DIR) -> None:
    """Show worker state summary."""
    runtime = _runtime(state_dir)
    observed = runtime.inspect()
    typer.echo(json.dumps(
        {
            "completed_units": observed.completed_units,
            "health": observed.health.value,
            "freshness": observed.runtime_freshness.value,
            "fingerprint": observed.fingerprint,
        },
        indent=2,
    ))


@app.command()
def demo() -> None:
    """Run init → step → swap-config → reconcile (copy-paste friendly)."""
    typer.echo("1/4 init")
    init(state_dir=DEFAULT_STATE_DIR, contract=DEFAULT_CONTRACT)
    typer.echo("2/4 step")
    step(count=10, state_dir=DEFAULT_STATE_DIR)
    typer.echo("3/4 swap-config")
    swap_config(config_hash="cfg-b", state_dir=DEFAULT_STATE_DIR)
    typer.echo("4/4 reconcile")
    reconcile(state_dir=DEFAULT_STATE_DIR, contract=DEFAULT_CONTRACT)
    typer.echo("Demo complete.")


if __name__ == "__main__":
    app()
