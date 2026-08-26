from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console

from research_harness.contract.loader import format_validation_error, load_contract, write_contract
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerStore

app = typer.Typer(
    name="research-harness",
    help="Reconciliation controller for research execution.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_CONTRACT_PATH = Path("contract.yaml")
DEFAULT_LEDGER_PATH = Path(".research-harness/ledger.db")

ContractPathOption = Annotated[
    Path,
    typer.Option("--contract", "-c", help="Path to the project contract YAML."),
]
LedgerPathOption = Annotated[
    Path,
    typer.Option("--ledger", help="Path to the SQLite run ledger."),
]


def _resolve_contract_path(contract: Path) -> Path:
    return contract.expanduser().resolve()


@app.command("init")
def init_project(
    project_id: Annotated[str, typer.Option("--id", prompt=True)] = "example",
    objective: Annotated[
        str,
        typer.Option("--objective", prompt=True),
    ] = "Determine whether X affects Y.",
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing contract.")] = False,
) -> None:
    """Create a starter project contract."""
    contract_path = _resolve_contract_path(contract)
    if contract_path.exists() and not force:
        console.print(f"[red]Contract already exists:[/red] {contract_path}")
        raise typer.Exit(code=1)

    starter = default_contract(project_id=project_id, objective=objective)
    write_contract(starter, contract_path)
    console.print(f"[green]Created contract:[/green] {contract_path}")


@app.command("validate")
def validate_contract(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
) -> None:
    """Validate a project contract file."""
    contract_path = _resolve_contract_path(contract)
    if not contract_path.exists():
        console.print(f"[red]Contract not found:[/red] {contract_path}")
        raise typer.Exit(code=1)

    try:
        loaded = load_contract(contract_path)
    except ValidationError as error:
        console.print(format_validation_error(error))
        raise typer.Exit(code=1) from error
    except (OSError, ValueError, TypeError) as error:
        console.print(f"[red]Failed to load contract:[/red] {error}")
        raise typer.Exit(code=1) from error

    console.print(f"[green]Contract valid[/green] (v{loaded.contract_version})")
    console.print(f"Project: {loaded.project.id}")
    console.print(f"Objective: {loaded.project.objective}")


@app.command("status")
def project_status(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    ledger: LedgerPathOption = DEFAULT_LEDGER_PATH,
) -> None:
    """Show current project status from contract and ledger."""
    contract_path = _resolve_contract_path(contract)
    if not contract_path.exists():
        console.print(f"[red]Contract not found:[/red] {contract_path}")
        raise typer.Exit(code=1)

    try:
        loaded = load_contract(contract_path)
    except ValidationError as error:
        console.print(format_validation_error(error))
        raise typer.Exit(code=1) from error

    ledger_path = ledger.expanduser().resolve()
    latest_event = None
    if ledger_path.exists():
        store = LedgerStore(ledger_path)
        latest_event = store.latest_event(project_id=loaded.project.id)

    console.print(f"Project: {loaded.project.id}")
    console.print("State: STOPPED")
    console.print("Runtime health: UNKNOWN")
    console.print("Progress: UNKNOWN")
    console.print("Desired fingerprint: (not computed)")
    console.print("Observed fingerprint: (not computed)")
    console.print("Current incident: none")
    console.print(f"Completed: 0 / {loaded.validity.expected_units}")
    if latest_event is None:
        console.print("Ledger: no events recorded")
    else:
        console.print(
            f"Ledger: latest event {latest_event.event_type.value} "
            f"at {latest_event.recorded_at.isoformat()}"
        )


@app.command("run")
def run_supervisor() -> None:
    """Start the reconciliation supervisor. (Not implemented in Slice 1.)"""
    console.print("[yellow]Supervisor not implemented yet.[/yellow]")


@app.command("reconcile")
def reconcile_once() -> None:
    """Run one reconciliation pass. (Not implemented in Slice 1.)"""
    console.print("[yellow]Reconciliation not implemented yet.[/yellow]")


@app.command("inspect")
def inspect_runtime() -> None:
    """Inspect runtime state. (Not implemented in Slice 1.)"""
    console.print("[yellow]Runtime inspection not implemented yet.[/yellow]")


@app.command("incidents")
def list_incidents() -> None:
    """List incidents. (Not implemented in Slice 1.)"""
    console.print("[yellow]Incident listing not implemented yet.[/yellow]")


@app.command("doctor")
def doctor() -> None:
    """Check local harness health. (Not implemented in Slice 1.)"""
    console.print("[yellow]Doctor checks not implemented yet.[/yellow]")


@app.command("stop")
def stop_supervisor() -> None:
    """Stop the supervisor. (Not implemented in Slice 1.)"""
    console.print("[yellow]Stop not implemented yet.[/yellow]")


if __name__ == "__main__":
    app()
