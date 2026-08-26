from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console

from research_harness.contract.loader import format_validation_error, load_contract, write_contract
from research_harness.contract.models import ProjectContract
from research_harness.contract.template import default_contract
from research_harness.incidents import IncidentStore
from research_harness.ledger import LedgerStore
from research_harness.reconciliation import Reconciler
from research_harness.runtime.assessment import OperationalAssessment, assess_operation
from research_harness.runtime.fingerprint import (
    compare_fingerprints,
    fingerprint_digest,
    select_relaunch_action,
)
from research_harness.runtime.io import load_fingerprint_file, write_fingerprint_file
from research_harness.runtime.loader import (
    BUILTIN_RUNTIMES,
    list_registered_runtimes,
    resolve_runtime_load_request,
)
from research_harness.runtime.mutation import remediate_preflight
from research_harness.supervisor import Supervisor, request_stop
from research_harness.supervisor.runtime_factory import create_runtime

app = typer.Typer(
    name="research-harness",
    help="Reconciliation controller for research execution.",
    no_args_is_help=True,
)
runtimes_app = typer.Typer(help="Discover project runtime adapters.")
app.add_typer(runtimes_app, name="runtimes")
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
RuntimeOption = Annotated[
    str | None,
    typer.Option(
        "--runtime",
        help="Built-in (file, failing-worker), registered plugin name, or module:callable.",
    ),
]
RuntimeEntrypointOption = Annotated[
    str | None,
    typer.Option(
        "--runtime-entrypoint",
        help="Explicit module:callable runtime factory (overrides --runtime).",
    ),
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


def _detect_runtime_kind(state_dir: Path) -> str:
    if (state_dir / "worker_state.json").exists():
        return "failing-worker"
    return "file"


def _build_runtime(
    *,
    contract: ProjectContract,
    state_dir: Path,
    runtime: str | None = None,
    entrypoint: str | None = None,
) -> tuple[str, object]:
    request = resolve_runtime_load_request(
        runtime=runtime,
        entrypoint=entrypoint,
        contract_runtime_loader=contract.runtime_loader,
        state_dir=state_dir,
    )
    desired_path = state_dir / "desired_fingerprint.json"
    pending_desired = load_fingerprint_file(desired_path) if desired_path.exists() else None
    try:
        runtime_adapter = create_runtime(
            request=request,
            project_id=contract.project.id,
            state_dir=state_dir,
            pending_desired=pending_desired,
        )
    except (ImportError, TypeError, ValueError) as error:
        console.print(f"[red]Failed to load runtime:[/red] {error}")
        raise typer.Exit(code=1) from error
    if request.entrypoint:
        label = f"entrypoint:{request.entrypoint}"
    else:
        label = request.label
    return label, runtime_adapter


@runtimes_app.command("list")
def list_runtimes() -> None:
    """List built-in and entry-point-registered runtime adapters."""
    console.print("[bold]Built-in runtimes[/bold]")
    for name in sorted(BUILTIN_RUNTIMES):
        console.print(f"  {name}")
    registered = list_registered_runtimes()
    if not registered:
        console.print("\n[bold]Registered runtimes[/bold]")
        console.print("  (none — projects register via [research_harness.runtimes] entry points)")
        return
    console.print("\n[bold]Registered runtimes[/bold]")
    for runtime in registered:
        console.print(f"  {runtime.name} -> {runtime.entrypoint}")


def _fingerprint_highlight_keys(contract: ProjectContract) -> list[str]:
    preferred = ("source_manifest_sha256", "git_sha", "config_hash")
    return [key for key in preferred if key in contract.fingerprint.fields]


def _format_fingerprint_summary(fields: dict[str, str], *, keys: list[str]) -> str:
    if not fields:
        return "(empty)"
    digest = fingerprint_digest(fields)
    highlights = [f"{key}={fields[key]}" for key in keys if key in fields]
    if highlights:
        return f"{digest} ({', '.join(highlights)})"
    return digest or "(empty)"


def _print_operational_assessment(
    *,
    assessment: OperationalAssessment,
    contract: ProjectContract,
    runtime_kind: str,
    project_id: str,
    lifecycle: str,
    observed_units: int,
    open_incidents: list[object],
    latest_event: object | None,
) -> None:
    fingerprints = assessment.fingerprints
    highlight_keys = _fingerprint_highlight_keys(contract)

    console.print(f"Project: {project_id}")
    console.print(f"Runtime: {runtime_kind}")
    console.print(f"Lifecycle: {lifecycle}")
    console.print(f"Runtime health: {assessment.runtime_health.value}")
    console.print(f"Progress: {assessment.progress.value}")
    console.print(f"Runtime freshness: {assessment.runtime_freshness.value}")
    console.print(f"Inspection: {assessment.inspection.value}")
    if assessment.reconciliation_required:
        console.print("Reconciliation: REQUIRED")
    elif assessment.repository_ahead:
        console.print("Reconciliation: NOT REQUIRED (repo ahead of desired)")
    else:
        console.print("Reconciliation: NOT REQUIRED")
    console.print(
        "Running fingerprint: "
        + _format_fingerprint_summary(fingerprints.running, keys=highlight_keys)
    )
    console.print(
        "Desired deployment: "
        + _format_fingerprint_summary(fingerprints.desired, keys=highlight_keys)
    )
    if fingerprints.repository is not None:
        console.print(
            "Repository fingerprint: "
            + _format_fingerprint_summary(fingerprints.repository, keys=highlight_keys)
        )
        console.print(f"Repo ahead of desired: {'YES' if assessment.repository_ahead else 'NO'}")
    else:
        console.print("Repository fingerprint: (not reported)")
    if open_incidents:
        incident = open_incidents[0]
        console.print(
            f"Current incident: {incident.incident_id} "
            f"[{incident.status.value}] {incident.symptom}"
        )
    else:
        console.print("Current incident: none")
    console.print(f"Completed: {observed_units} / {contract.validity.expected_units}")
    if latest_event is None:
        console.print("Ledger: no events recorded")
    else:
        console.print(
            f"Ledger: latest event {latest_event.event_type.value} "
            f"at {latest_event.recorded_at.isoformat()}"
        )


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
    runtime: RuntimeOption = None,
    entrypoint: RuntimeEntrypointOption = None,
) -> None:
    """Show current project status from contract, runtime, and ledger."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime=runtime,
        entrypoint=entrypoint,
    )
    observed = runtime_adapter.inspect()
    assessment = assess_operation(
        runtime=runtime_adapter,  # type: ignore[arg-type]
        contract=loaded,
        state_dir=state_path,
        runtime_kind=kind,
        observed=observed,
    )

    ledger_path = _resolve_ledger_path(ledger, state_path)
    latest_event = None
    if ledger_path.exists():
        store = LedgerStore(ledger_path)
        latest_event = store.latest_event(project_id=loaded.project.id)

    incident_store = IncidentStore(state_path / "incidents.db")
    open_incidents = incident_store.list_open(project_id=loaded.project.id)

    _print_operational_assessment(
        assessment=assessment,
        contract=loaded,
        runtime_kind=kind,
        project_id=loaded.project.id,
        lifecycle=observed.lifecycle.value,
        observed_units=observed.completed_units,
        open_incidents=open_incidents,
        latest_event=latest_event,
    )


@app.command("promote")
def promote_desired(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
    runtime: RuntimeOption = None,
    entrypoint: RuntimeEntrypointOption = None,
    source: Annotated[
        str,
        typer.Option(
            "--from",
            help="Promote desired deployment from repository, running, or a JSON file path.",
        ),
    ] = "repository",
) -> None:
    """Promote a fingerprint into desired deployment (explicit deployment intent)."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime=runtime,
        entrypoint=entrypoint,
    )
    observed = runtime_adapter.inspect()
    if source in {"repository", "repo"}:
        from research_harness.runtime.assessment import repository_fingerprint_for

        repository = repository_fingerprint_for(runtime_adapter, state_dir=state_path)  # type: ignore[arg-type]
        if repository is None:
            console.print(
                "[red]Repository fingerprint unavailable.[/red] "
                "Implement repository_fingerprint() on the adapter or write "
                f"{state_path / 'repository_fingerprint.json'}."
            )
            raise typer.Exit(code=1)
        promoted = repository
    elif source == "running":
        promoted = dict(observed.fingerprint)
    else:
        source_path = _resolve(Path(source))
        if not source_path.exists():
            console.print(f"[red]Fingerprint file not found:[/red] {source_path}")
            raise typer.Exit(code=1)
        promoted = load_fingerprint_file(source_path)

    desired_path = state_path / "desired_fingerprint.json"
    write_fingerprint_file(desired_path, promoted)
    assessment = assess_operation(
        runtime=runtime_adapter,  # type: ignore[arg-type]
        contract=loaded,
        state_dir=state_path,
        runtime_kind=kind,
        observed=observed,
    )
    console.print(f"[green]Promoted desired deployment[/green] → {desired_path}")
    console.print(
        "Desired deployment: "
        + _format_fingerprint_summary(
            assessment.fingerprints.desired,
            keys=_fingerprint_highlight_keys(loaded),
        )
    )
    console.print(f"Runtime freshness: {assessment.runtime_freshness.value}")
    if assessment.reconciliation_required:
        comparison = compare_fingerprints(
            desired=assessment.fingerprints.desired,
            observed=assessment.fingerprints.running,
            fields=loaded.fingerprint.fields,
        )
        action = select_relaunch_action(loaded.fingerprint, comparison)
        console.print(f"Reconciliation required — proposed action: {action or 'none'}")


@app.command("reconcile")
def reconcile_once(
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    ledger: LedgerPathOption = DEFAULT_LEDGER_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
    runtime: RuntimeOption = None,
    entrypoint: RuntimeEntrypointOption = None,
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
    state_path = _resolve(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    ledger_path = _resolve_ledger_path(ledger, state_path)
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

    _kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime=runtime,
        entrypoint=entrypoint,
    )
    observed_path_attr = getattr(runtime_adapter, "observed_path", None)
    if observed_path_attr is not None:
        runtime_adapter.observed_path = observed_path  # type: ignore[attr-defined]
        if not observed_path.exists():
            write_fingerprint_file(observed_path, {})
    setter = getattr(runtime_adapter, "set_pending_desired", None)
    if callable(setter):
        setter(desired_fp)

    store = LedgerStore(ledger_path)
    reconciler = Reconciler(
        contract=loaded,
        runtime=runtime_adapter,  # type: ignore[arg-type]
        ledger=store,
        persist_desired_path=desired_path,
    )
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
    runtime: RuntimeOption = None,
    entrypoint: RuntimeEntrypointOption = None,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Seconds between reconciliation ticks."),
    ] = 1.0,
    max_ticks: Annotated[
        int | None,
        typer.Option("--max-ticks", help="Stop after N ticks (for tests)."),
    ] = None,
    observe_only: Annotated[
        bool,
        typer.Option("--observe-only", help="Plan actions without mutating runtime."),
    ] = False,
) -> None:
    """Start the reconciliation supervisor loop."""
    state_path = _resolve(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    ledger_path = _resolve_ledger_path(ledger, state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime=runtime,
        entrypoint=entrypoint,
    )
    store = LedgerStore(ledger_path)
    supervisor = Supervisor(
        contract=loaded,
        runtime=runtime_adapter,  # type: ignore[arg-type]
        state_dir=state_path,
        ledger=store,
        runtime_kind=kind,
        observe_only=observe_only,
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
    runtime: RuntimeOption = None,
    entrypoint: RuntimeEntrypointOption = None,
) -> None:
    """Inspect observed runtime state."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime=runtime,
        entrypoint=entrypoint,
    )
    observed = runtime_adapter.inspect()
    assessment = assess_operation(
        runtime=runtime_adapter,  # type: ignore[arg-type]
        contract=loaded,
        state_dir=state_path,
        runtime_kind=kind,
        observed=observed,
    )
    console.print(f"Project: {observed.project_id}")
    console.print(f"Runtime: {kind}")
    console.print(f"Lifecycle: {observed.lifecycle.value}")
    console.print(f"Runtime health: {assessment.runtime_health.value}")
    console.print(f"Progress: {assessment.progress.value}")
    console.print(f"Runtime freshness: {assessment.runtime_freshness.value}")
    console.print(f"Inspection: {assessment.inspection.value}")
    console.print(f"Completed units: {observed.completed_units}")
    console.print(
        "Running fingerprint: "
        + _format_fingerprint_summary(
            assessment.fingerprints.running,
            keys=_fingerprint_highlight_keys(loaded),
        )
    )


@app.command("preflight")
def mutation_preflight(
    action: Annotated[str, typer.Argument(help="Contemplated mutation action.")],
    contract: ContractPathOption = DEFAULT_CONTRACT_PATH,
    state_dir: StateDirOption = DEFAULT_STATE_DIR,
    runtime: RuntimeOption = None,
    entrypoint: RuntimeEntrypointOption = None,
    repair: Annotated[
        bool,
        typer.Option(
            "--repair",
            help="Apply permitted prerequisite repairs and re-run preflight.",
        ),
    ] = False,
) -> None:
    """Run project mutation preflight for a contemplated action."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime=runtime,
        entrypoint=entrypoint,
    )
    if repair:
        remediation = remediate_preflight(runtime_adapter, action)  # type: ignore[arg-type]
        for repair_id in remediation.repairs_applied:
            console.print(f"[green]Applied repair:[/green] {repair_id}")
        readiness = remediation.final
    else:
        observed = runtime_adapter.inspect()
        assessment = assess_operation(
            runtime=runtime_adapter,  # type: ignore[arg-type]
            contract=loaded,
            state_dir=state_path,
            runtime_kind=kind,
            observed=observed,
            mutation_action=action,
        )
        readiness = assessment.mutation
    if readiness is None:
        console.print("[red]Mutation preflight unavailable.[/red]")
        raise typer.Exit(code=1)
    console.print(f"Action: {readiness.action}")
    console.print(f"Mutation readiness: {readiness.status.value}")
    if readiness.reason:
        console.print(f"Reason: {readiness.reason}")
    for check in readiness.checks:
        mark = "✓" if check.passed else "✗"
        detail = f" — {check.detail}" if check.detail else ""
        console.print(f"{mark} {check.name}{detail}")
    for repair_item in readiness.repairs:
        desc = f" — {repair_item.description}" if repair_item.description else ""
        console.print(f"→ repair required: {repair_item.repair_id}{desc}")
    if readiness.status.value == "READY":
        return
    if readiness.status.value == "REPAIRABLE" and not repair:
        console.print("[yellow]Run with --repair to apply permitted prerequisite fixes.[/yellow]")
    raise typer.Exit(code=1)


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
    runtime: RuntimeOption = None,
    entrypoint: RuntimeEntrypointOption = None,
) -> None:
    """Check local harness health."""
    state_path = _resolve(state_dir)
    loaded = _load_contract_or_exit(contract, state_dir=state_path)
    ledger_path = _resolve_ledger_path(ledger, state_path)
    kind, runtime_adapter = _build_runtime(
        contract=loaded,
        state_dir=state_path,
        runtime=runtime,
        entrypoint=entrypoint,
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
