#!/usr/bin/env python3
"""Run the deterministic failing_worker example workload."""

from __future__ import annotations

from pathlib import Path

import typer

from research_harness.adapters.failing_worker import FailingWorkerRuntime, WorkerConfig

app = typer.Typer(help="Deterministic failing_worker research workload.")


@app.command()
def init(state_dir: Path = Path("examples/failing_worker/.state")) -> None:
    """Initialize worker config and state."""
    state_dir.mkdir(parents=True, exist_ok=True)
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=state_dir)
    runtime.store.save_config(WorkerConfig())
    runtime.store.save_state(runtime.store.load_state())
    typer.echo(f"Initialized failing_worker state in {state_dir}")


@app.command()
def step(
    count: int = typer.Option(1, help="Units to process."),
    state_dir: Path = Path("examples/failing_worker/.state"),
) -> None:
    """Process experiment units."""
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=state_dir)
    state = runtime.process_units(count=count)
    effect = runtime.current_effect_size()
    typer.echo(
        f"units={state.completed_units} running={state.running} effect_size={effect:.4f}"
    )


@app.command()
def swap_config(
    config_hash: str = typer.Option("cfg-b", help="Pending config hash requiring relaunch."),
    state_dir: Path = Path("examples/failing_worker/.state"),
) -> None:
    """Stage a config swap that requires harness relaunch to apply."""
    runtime = FailingWorkerRuntime(project_id="failing-worker", state_dir=state_dir)
    runtime.set_pending_config(config_hash)
    typer.echo(f"Pending config_hash={config_hash} (restart required)")


if __name__ == "__main__":
    app()
