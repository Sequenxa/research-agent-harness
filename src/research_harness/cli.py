from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console

from research_harness.adapters.file_runtime import FileRuntimeAdapter
from research_harness.contract.loader import format_validation_error, load_contract, write_contract
from research_harness.contract.models import ProjectContract
from research_harness.contract.template import default_contract
from research_harness.ledger import LedgerStore
from research_harness.reconciliation import Reconciler
from research_harness.runtime.fingerprint import compare_fingerprints, fingerprint_digest
from research_harness.runtime.io import load_fingerprint_file, write_fingerprint_file

app = typer.Typer(
    name="research-harness",
    help="Reconciliation controller for research execution.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_CONTRACT_PATH = Path("contract.yaml")
DEFAULT_STATE_DIR = Path(".research-harness")
DEFAULT_LEDGER_PATH = DEFAULT_STATE_DIR / "ledger.db"
DEFAULT_DESIRED_PATH = DEFAULT_STATE_DIR / "desired_fingerprint.json"
DEFAULT_OBSERVED_PATH = DEFAULT_STATE_DIR / "observed_fingerprint.json"

ContractPathOption = Annotated[
    Path,
    typer.Option("--contract", "-c", help="Path to the project contract YAML."),
]
LedgerPathOption = Annotated[
    Path,
    typer.Option("--ledger", help="Path to the SQLite run ledger."),
]
StateDirOption = Annotated[
    Path,
    typer.Option("--state-dir", help="Directory for local runtime fingerprint state."),
]


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _load_contract_or_exit(contract_path: Path) -> ProjectContract:
    if not contract_path.exists():
        console.print(f"[red]Contract not found:[/red] {contract_path}")
        raise typer.Exit(code=1)
    try:
        return load_contract(contract_path)
    except ValidationError as error:
        console.print(format_validation_error(error))
        raise typer.Exit(code=1) from error
    except (OSError, ValueError, TypeError) as error:
        console.print(f"[red]Failed to load contract:[/red] {error}")
        raise typer.Exit(code=1) from error


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
    contract_path = _resolve(contract)
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
    loaded = _load_contract_or_exit(_resolve(contract))
    console.print(f"[green]Contract valid[/green] (v{loaded.contract_version})")
    console.print(f"Project: {loaded.project.id}")
    console.print(f"Objective: {loaded.project.objective}")


@app.command("status")
def project_status(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    ledger: LedgerPathOption = DEFAULT_LEDGER_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
) -> None:
    """Show current project status from contract, fingerprints, and ledger."""
    loaded = _load_contract_or_exit(_resolve(contract))
    state_path = _resolve(state_dir)
    desired_path = state_path / "desired_fingerprint.json"
    observed_path = state_path / "observed_fingerprint.json"

    desired_fp: dict[str, str] = {}
    observed_fp: dict[str, str] = {}
    freshness = "UNKNOWN"
    if desired_path.exists() and observed_path.exists():
        desired_fp = load_fingerprint_file(desired_path)
        observed_fp = load_fingerprint_file(observed_path)
        comparison = compare_fingerprints(
            desired=desired_fp,
            observed=observed_fp,
            fields=loaded.fingerprint.fields,
        )
        freshness = comparison.freshness.value

    ledger_path = _resolve(ledger)
    latest_event = None
    if ledger_path.exists():
        store = LedgerStore(ledger_path)
        latest_event = store.latest_event(project_id=loaded.project.id)

    console.print(f"Project: {loaded.project.id}")
    console.print("State: STOPPED" if not observed_fp else "State: RUNNING")
    console.print("Runtime health: UNKNOWN")
    console.print("Progress: UNKNOWN")
    console.print(f"Runtime freshness: {freshness}")
    console.print(
        "Desired fingerprint: "
        + (fingerprint_digest(desired_fp) if desired_fp else "(not set)")
    )
    console.print(
        "Observed fingerprint: "
        + (fingerprint_digest(observed_fp) if observed_fp else "(not set)")
    )
    console.print("Current incident: none")
    console.print(f"Completed: 0 / {loaded.validity.expected_units}")
    if latest_event is None:
        console.print("Ledger: no events recorded")
    else:
        console.print(
            f"Ledger: latest event {latest_event.event_type.value} "
            f"at {latest_event.recorded_at.isoformat()}"
        )


@app.command("reconcile")
def reconcile_once(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    ledger: LedgerPathOption = DEFAULT_LEDGER_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
    desired: Annotated[
        Path | None,
        typer.Option("--desired", help="JSON file with desired fingerprint fields."),
    ] = None,
    observed: Annotated[
        Path | None,
        typer.Option("--observed", help="JSON file with observed fingerprint fields."),
    ] = None,
) -> None:
    """Run one reconciliation pass against local fingerprint state."""
    loaded = _load_contract_or_exit(_resolve(contract))
    state_path = _resolve(state_dir)
    desired_path = _resolve(desired) if desired else state_path / "desired_fingerprint.json"
    observed_path = _resolve(observed) if observed else state_path / "observed_fingerprint.json"

    if not desired_path.exists():
        console.print(f"[red]Desired fingerprint not found:[/red] {desired_path}")
        console.print(
            "Create it with fingerprint field values matching contract.fingerprint.fields"
        )
        raise typer.Exit(code=1)

    desired_fp = load_fingerprint_file(desired_path)
    if not observed_path.exists():
        # Seed observed from empty/missing as fully stale {}.
        write_fingerprint_file(observed_path, {})

    runtime = FileRuntimeAdapter(
        project_id=loaded.project.id,
        state_dir=state_path,
        pending_desired=desired_fp,
    )
    # Ensure observed path used by adapter matches CLI path when custom.
    if observed is not None:
        runtime.observed_path = observed_path
        if not observed_path.exists():
            write_fingerprint_file(observed_path, {})

    store = LedgerStore(_resolve(ledger))
    reconciler = Reconciler(contract=loaded, runtime=runtime, ledger=store)
    result = reconciler.reconcile(desired_fingerprint=desired_fp)

    if result.success and not result.differences:
        console.print("[green]Runtime current — no reconciliation needed.[/green]")
    elif result.success:
        console.print("[green]Reconciled stale runtime.[/green]")
        console.print(f"Actions: {', '.join(result.actions_taken) or '(none)'}")
        for diff in result.differences:
            console.print(f"  - {diff.field}: {diff.observed!r} → {diff.desired!r} ({diff.action})")
    else:
        console.print("[red]Reconciliation blocked or incomplete.[/red]")
        if result.blocked_reason:
            console.print(result.blocked_reason)
        raise typer.Exit(code=1)


@app.command("run")
def run_supervisor() -> None:
    """Start the reconciliation supervisor. (Not implemented yet.)"""
    console.print("[yellow]Supervisor not implemented yet.[/yellow]")


@app.command("inspect")
def inspect_runtime(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
) -> None:
    """Inspect local observed runtime fingerprint."""
    loaded = _load_contract_or_exit(_resolve(contract))
    runtime = FileRuntimeAdapter(project_id=loaded.project.id, state_dir=_resolve(state_dir))
    observed = runtime.inspect()
    console.print(f"Project: {observed.project_id}")
    console.print(f"Lifecycle: {observed.lifecycle.value}")
    console.print(f"Health: {observed.health.value}")
    console.print(f"Fingerprint: {fingerprint_digest(observed.fingerprint) or '(empty)'}")


@app.command("incidents")
def list_incidents(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
) -> None:
    """List open incidents for the project."""
    from research_harness.incidents import IncidentStore

    loaded = _load_contract_or_exit(_resolve(contract))
    store = IncidentStore(_resolve(state_dir) / "incidents.db")
    open_incidents = store.list_open(project_id=loaded.project.id)
    if not open_incidents:
        console.print("No open incidents.")
        return
    for incident in open_incidents:
        console.print(
            f"{incident.incident_id} [{incident.status.value}] "
            f"{incident.symptom} stage={incident.stage.value}"
        )


@app.command("doctor")
def doctor() -> None:
    """Check local harness health. (Not implemented yet.)"""
    console.print("[yellow]Doctor checks not implemented yet.[/yellow]")


@app.command("stop")
def stop_supervisor() -> None:
    """Stop the supervisor. (Not implemented yet.)"""
    console.print("[yellow]Stop not implemented yet.[/yellow]")


if __name__ == "__main__":
    app()
