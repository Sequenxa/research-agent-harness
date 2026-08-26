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
from research_harness.incidents import IncidentStore
from research_harness.ledger import LedgerStore
from research_harness.reconciliation import Reconciler
from research_harness.runtime.fingerprint import compare_fingerprints, fingerprint_digest
from research_harness.runtime.io import load_fingerprint_file, write_fingerprint_file
from research_harness.supervisor import Supervisor, request_stop
from research_harness.supervisor.loop import RuntimeKind
from research_harness.supervisor.runtime_factory import create_runtime, desired_fingerprint_for

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


def _resolve_contract_path(contract: Path, state_dir: Path) -> Path:
    """Resolve contract path, inferring from state_dir when default is missing."""
    resolved = _resolve(contract)
    if resolved.exists():
        return resolved
    if contract != DEFAULT_CONTRACT_PATH:
        return resolved
    state_path = _resolve(state_dir)
    for candidate in (state_path.parent / "contract.yaml", state_path / "contract.yaml"):
        if candidate.exists():
            return candidate.resolve()
    return resolved


def _resolve_ledger_path(ledger: Path, state_dir: Path) -> Path:
    """Use state_dir/ledger.db when the default ledger path was not overridden."""
    if ledger != DEFAULT_LEDGER_PATH:
        return _resolve(ledger)
    return _resolve(state_dir) / "ledger.db"


def _load_contract_or_exit(contract_path: Path, *, state_dir: Path | None = None) -> ProjectContract:
    path = (
        _resolve_contract_path(contract_path, state_dir)
        if state_dir is not None
        else _resolve(contract_path)
    )
    if not path.exists():
        console.print(f"[red]Contract not found:[/red] {path}")
        console.print(
            "Pass --contract explicitly, or place contract.yaml beside --state-dir."
        )
        raise typer.Exit(code=1)
    try:
        return load_contract(path)
    except ValidationError as error:
        console.print(format_validation_error(error))
        raise typer.Exit(code=1) from error
    except (OSError, ValueError, TypeError) as error:
        console.print(f"[red]Failed to load contract:[/red] {error}")
        raise typer.Exit(code=1) from error


def _detect_runtime_kind(state_dir: Path) -> RuntimeKind:
    if (state_dir / "worker_state.json").exists():
        return "failing-worker"
    return "file"


def _build_runtime(
    *,
    contract: ProjectContract,
    state_dir: Path,
    runtime_kind: RuntimeKind | None = None,
) -> tuple[RuntimeKind, object]:
    kind = runtime_kind or _detect_runtime_kind(state_dir)
    desired_path = state_dir / "desired_fingerprint.json"
    pending_desired = load_fingerprint_file(desired_path) if desired_path.exists() else None
    runtime = create_runtime(
        kind=kind,
        project_id=contract.project.id,
        state_dir=state_dir,
        pending_desired=pending_desired,
    )
    return kind, runtime


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
    runtime: Annotated[
        str | None,
        typer.Option("--runtime", help="Runtime adapter: file or failing-worker."),
    ] = None,
) -> None:
    """Show current project status from contract, runtime, and ledger."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime_kind=runtime if runtime in {"file", "failing-worker"} else None,  # type: ignore[arg-type]
    )
    observed = runtime_adapter.inspect()
    desired_fp = desired_fingerprint_for(
        runtime_adapter,  # type: ignore[arg-type]
        state_dir=state_path,
        runtime_kind=kind,
    )
    comparison = compare_fingerprints(
        desired=desired_fp,
        observed=observed.fingerprint,
        fields=loaded.fingerprint.fields,
    )

    ledger_path = _resolve_ledger_path(ledger, state_path)
    latest_event = None
    if ledger_path.exists():
        store = LedgerStore(ledger_path)
        latest_event = store.latest_event(project_id=loaded.project.id)

    incident_store = IncidentStore(state_path / "incidents.db")
    open_incidents = incident_store.list_open(project_id=loaded.project.id)

    console.print(f"Project: {loaded.project.id}")
    console.print(f"Runtime: {kind}")
    console.print(f"Lifecycle: {observed.lifecycle.value}")
    console.print(f"Health: {observed.health.value}")
    console.print(f"Progress: {observed.progress.value}")
    console.print(f"Runtime freshness: {comparison.freshness.value}")
    console.print(f"Desired fingerprint: {fingerprint_digest(desired_fp) or '(not set)'}")
    console.print(
        f"Observed fingerprint: {fingerprint_digest(observed.fingerprint) or '(empty)'}"
    )
    if open_incidents:
        incident = open_incidents[0]
        console.print(
            f"Current incident: {incident.incident_id} "
            f"[{incident.status.value}] {incident.symptom}"
        )
    else:
        console.print("Current incident: none")
    console.print(f"Completed: {observed.completed_units} / {loaded.validity.expected_units}")
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
def run_supervisor(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    ledger: LedgerPathOption = DEFAULT_LEDGER_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
    runtime: Annotated[
        str | None,
        typer.Option("--runtime", help="Runtime adapter: file or failing-worker."),
    ] = None,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Seconds between reconciliation ticks."),
    ] = 1.0,
    max_ticks: Annotated[
        int | None,
        typer.Option("--max-ticks", help="Stop after N ticks (for tests)."),
    ] = None,
) -> None:
    """Start the reconciliation supervisor loop."""
    state_path = _resolve(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    ledger_path = _resolve_ledger_path(ledger, state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime_kind=runtime if runtime in {"file", "failing-worker"} else None,  # type: ignore[arg-type]
    )
    store = LedgerStore(ledger_path)
    supervisor = Supervisor(
        contract=loaded,
        runtime=runtime_adapter,  # type: ignore[arg-type]
        state_dir=state_path,
        ledger=store,
        runtime_kind=kind,
    )
    try:
        results = supervisor.run(interval_seconds=interval, max_ticks=max_ticks)
    except RuntimeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
    for index, tick in enumerate(results, start=1):
        action_summary = ", ".join(tick.actions) if tick.actions else "noop"
        console.print(
            f"tick {index}: lifecycle={tick.observed.lifecycle.value} "
            f"health={tick.observed.health.value} "
            f"units={tick.observed.completed_units} actions={action_summary}"
        )
        if tick.message:
            console.print(f"  {tick.message}")
    console.print(f"[green]Supervisor stopped after {len(results)} tick(s).[/green]")


@app.command("inspect")
def inspect_runtime(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
    runtime: Annotated[
        str | None,
        typer.Option("--runtime", help="Runtime adapter: file or failing-worker."),
    ] = None,
) -> None:
    """Inspect observed runtime state."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime_kind=runtime if runtime in {"file", "failing-worker"} else None,  # type: ignore[arg-type]
    )
    observed = runtime_adapter.inspect()
    console.print(f"Project: {observed.project_id}")
    console.print(f"Runtime: {kind}")
    console.print(f"Lifecycle: {observed.lifecycle.value}")
    console.print(f"Health: {observed.health.value}")
    console.print(f"Progress: {observed.progress.value}")
    console.print(f"Freshness: {observed.runtime_freshness.value}")
    console.print(f"Completed units: {observed.completed_units}")
    console.print(f"Fingerprint: {fingerprint_digest(observed.fingerprint) or '(empty)'}")


@app.command("incidents")
def list_incidents(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
) -> None:
    """List open incidents for the project."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    store = IncidentStore(state_path / "incidents.db")
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
def doctor(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    ledger: LedgerPathOption = DEFAULT_LEDGER_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
    runtime: Annotated[
        str | None,
        typer.Option("--runtime", help="Runtime adapter: file or failing-worker."),
    ] = None,
) -> None:
    """Check local harness health."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    ledger_path = _resolve_ledger_path(ledger, state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime_kind=runtime if runtime in {"file", "failing-worker"} else None,  # type: ignore[arg-type]
    )
    supervisor = Supervisor(
        contract=loaded,
        runtime=runtime_adapter,  # type: ignore[arg-type]
        state_dir=state_path,
        ledger=LedgerStore(ledger_path),
        runtime_kind=kind,
    )
    report = supervisor.doctor()
    for check in report.checks:
        console.print(check)
    if report.ok:
        console.print("[green]Doctor: ok[/green]")
    else:
        console.print("[red]Doctor: issues found[/red]")
        raise typer.Exit(code=1)


@app.command("stop")
def stop_supervisor(
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
) -> None:
    """Request supervisor stop via flag file."""
    request_stop(_resolve(state_dir))
    console.print("[green]Stop flag set. Supervisor will exit on next tick.[/green]")


if __name__ == "__main__":
    app()
