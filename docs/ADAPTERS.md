# Implementing Adapters

This guide is for **external research repos** that want to run under the harness. v0.1 ships three adapter interfaces only — keep implementations thin and project-specific.

## v0.1 adapter scope

| Adapter | Responsibility |
|---------|----------------|
| `RuntimeAdapter` | Inspect live runtime; restart or relaunch workers |
| `CheckpointAdapter` | Read/write experiment checkpoints for resume |
| `DiagnosticsAdapter` | Collect evidence (logs, metrics, exit codes) for incidents |

Do **not** implement provider SDKs, schedulers, or eval frameworks inside the harness core. Those belong in your repo as adapters.

## Reference implementation

Study these in order:

1. [`examples/failing_worker/`](../examples/failing_worker/) — file-backed worker you can run locally
2. [`src/research_harness/adapters/failing_worker.py`](../src/research_harness/adapters/failing_worker.py) — `FailingWorkerRuntime` (implements Runtime + Checkpoint)
3. [`src/research_harness/adapters/fake_worker.py`](../src/research_harness/adapters/fake_worker.py) — in-memory worker for acceptance tests

Run the example:

```bash
uv sync --dev
uv run python examples/failing_worker/run.py demo
```

## RuntimeAdapter

Your runtime adapter must answer: **what is running right now?** and **how do I restart it?**

```python
from research_harness.adapters.base import RuntimeAdapter
from research_harness.models.enums import Health, Lifecycle, Progress, RuntimeFreshness
from research_harness.models.state import ObservedState

class MyProjectRuntime(RuntimeAdapter):
    def inspect(self) -> ObservedState:
        # Read process state, config actually applied, progress watermarks.
        return ObservedState(
            project_id="my-project",
            observed_at=...,
            lifecycle=Lifecycle.RUNNING,
            health=Health.HEALTHY,
            progress=Progress.ADVANCING,
            runtime_freshness=RuntimeFreshness.CURRENT,
            fingerprint={
                "git_sha": "...",
                "model": "...",
                "config_hash": "...",  # applied, not merely edited on disk
            },
            completed_units=42,
            last_progress_at=...,
        )

    def restart_worker(self) -> None:
        # Restart worker process; resume from checkpoint if applicable.

    def relaunch(self, action: str) -> None:
        # Apply graded relaunch: worker_restart | service_restart | full_relaunch | ...

    def stop(self) -> None:
        # Stop the workload; inspect() must report Lifecycle.STOPPED afterward.

    def mutation_preflight(self, action: str) -> MutationReadiness:
        # Project-specific safety gate before the harness mutates runtime.
        # Return REPAIRABLE when a standing-authorized prerequisite can be fixed first.
        checks = [
            MutationPreflightCheck(name="authorization_rebuild", passed=False,
                                   detail="authorization/source rebuild mismatch"),
        ]
        return MutationReadiness.repairable(
            "full_relaunch",
            reason="scheduler authorization must be refreshed",
            repairs=[
                MutationRepair(
                    repair_id="refresh_scheduler_authorization",
                    description="Rebuild scheduler authorization from current source",
                )
            ],
            checks=checks,
        )

    def repair_mutation_prerequisite(self, repair_id: str) -> MutationRepairResult:
        # Apply + verify one permitted prerequisite repair.
        return MutationRepairResult.ok(repair_id)

    def fingerprint_field_classifications(self) -> dict[str, str]:
        # deployment | research_semantic | authorization_sensitive
        # Unclassified fields are NOT auto-synced into desired after repair.
        return {
            "source_manifest_sha256": "deployment",
            "models_toml_sha256": "research_semantic",
        }

    def repository_fingerprint(self) -> dict[str, str] | None:
        # Optional: current repository/source manifest (may differ from running).
        return {"git_sha": "...", "config_hash": "..."}
```

### Operational signals (orthogonal)

Do not collapse native scheduler health into harness inspection health:

| Signal | Question |
|--------|----------|
| `runtime_health` | Is the workload itself functioning? |
| `progress` | Is scientific/operational work advancing? |
| `runtime_freshness` | Is running == desired deployment? |
| `inspection` | Can the harness observe state? |
| `mutation_readiness` | Is a contemplated mutation safe right now? |
| `scheduled_path_armed` | Is the project's scheduled path loaded/armed? |

| Preflight status | Meaning | Harness action |
|------------------|---------|----------------|
| `READY` | Can mutate now | Proceed |
| `REPAIRABLE` | Permitted prerequisite must be fixed first | Apply repairs, verify, re-run preflight |
| `BLOCKED` | Requires authority the harness does not have | Stop mutation and escalate |

Observe and diagnose are always permitted when inspection works. Plan remediation is permitted. **Mutate/relaunch** requires passing `mutation_preflight(action)` (after any REPAIRABLE remediation) in addition to harness authority.

`research-harness status` runs an **observe-only incident evaluation**: stalls, suspect progress, and drain-held conditions open rows in `incidents.db` even when the supervisor is not allowed to mutate.

```bash
research-harness status
research-harness preflight full_relaunch
research-harness preflight full_relaunch --repair
research-harness promote --from repository
```

### Durable progress (do not lie)

Stall detection compares watermark `last_advanced_at` to wall clock. If your adapter sets:

```python
now = datetime.now(UTC)
return ObservedState(observed_at=now, last_progress_at=now, ...)
```

…then `stall_after` never fires. The harness treats `last_progress_at` / watermark timestamps **equal to `observed_at`** (within `progress.suspect_progress_within`, default `0s`) as **suspect progress** and opens a `suspect_progress` incident.

Use durable sources (unit file mtimes, checkpoint timestamps, ledger watermarks) — never stamp progress with the inspect clock.

### Scheduled path / drain hold

When a recovery or drain mode disarms the project's scheduled path, expose:

```python
return ObservedState(
    ...,
    scheduled_path_armed=False,  # or True when calendar/cron/LaunchAgents are loaded
    extra={"mode": "recovery-active"},  # or ProgressContext.operational_mode
)
```

Contract (optional):

```yaml
progress:
  scheduled_path_disarmed_stall_after: 12h
  drain_modes: [recovery, drain, recovery-active, recovery_active]
```

If mode is a drain/recovery mode, `scheduled_path_armed` is false, and watermarks are not advancing past that duration, the watchdog symptom is `scheduled_path_held_by_drain`. Do **not** bake LaunchAgent specifics into the harness — only this boolean + mode signal.

### Recovery budgets vs project drivers

`recovery.max_identical_attempts` and oscillation detection apply only to **harness remediation strategies** (`worker_restart`, `service_restart`, `full_relaunch`, …). They do **not** auto-stop project-owned recovery/driver loops. Drivers must enforce their own hard-stops; the harness detects the stuck *state* via watermarks + `scheduled_path_armed`.

### Fingerprint rules

Three distinct fingerprints — do not collapse them:

| Fingerprint | Meaning |
|-------------|---------|
| **Running** | What the live workload is actually using (`inspect().fingerprint`) |
| **Desired deployment** | What the harness should reconcile toward (`desired_fingerprint.json` or explicit promotion) |
| **Repository** | Current source tree / manifest (`repository_fingerprint()` or `repository_fingerprint.json`) |

- **Runtime freshness** compares desired deployment vs running — not repository vs running.
- Repository moving ahead of desired is normal during development and does **not** trigger reconciliation until you **promote**:
  ```bash
  research-harness promote --from repository
  ```
- If desired and running differ, runtime is **stale** and reconciliation may relaunch (when `authority.runtime_restarts` allows).

Do not report pending config edits as the observed fingerprint. See the `failing_worker` fix: staged swaps stay stale until relaunch applies them.

Declare which fields matter in your project contract:

```yaml
fingerprint:
  fields: [git_sha, lock_hash, model, provider, prompt_version, config_hash]
  on_change:
    config_hash: worker_restart
    model: full_relaunch
  default: full_relaunch
```

## CheckpointAdapter

```python
from research_harness.adapters.base import CheckpointAdapter

class MyProjectCheckpoint(CheckpointAdapter):
    def latest_checkpoint(self) -> dict[str, object] | None:
        # Return {"completed_units": 100, "shard": 3, ...} or None.

    def save_checkpoint(self, payload: dict[str, object]) -> str:
        # Persist and return checkpoint id.
```

The incident engine resumes from `latest_checkpoint()` after `worker_restart` when recovery is authorized.

## DiagnosticsAdapter

```python
from research_harness.adapters.base import DiagnosticsAdapter

class MyProjectDiagnostics(DiagnosticsAdapter):
    def collect(self, *, symptom: str) -> dict[str, object]:
        # Return evidence: log tails, exit codes, metric snapshots.
        # Evidence is not an LLM explanation.
        return {"symptom": symptom, "exit_code": 1, "log_tail": "..."}
```

Wire this when building diagnosis steps in your supervisor integration. v0.1 core invokes it at the interface level; your repo provides the implementation.

## Project contract

Every project needs a validated contract YAML (schema v1.1). Generate a starter:

```bash
uv run research-harness init --id my-project --objective "..." --contract ./contract.yaml
# Optional: scaffold experiment/plan.json + schedule and wire contract.experiment
uv run research-harness init --id my-project --objective "..." --with-experiment --planned-units 4
uv run research-harness validate --contract ./contract.yaml
```

Key sections for adapters:

| Section | Adapter impact |
|---------|----------------|
| `fingerprint` | Which fields you must populate in `inspect()` |
| `progress.watermarks` | What stall detection expects (ledger vs adapter sources) |
| `progress.suspect_progress_within` | Reject inspect-clock progress stamps (default `0s`) |
| `progress.scheduled_path_disarmed_stall_after` | Escalate when drain holds the scheduled path disarmed |
| `authority` | What recovery actions are allowed |
| `recovery` | Harness remediation budgets only (not project driver loops) |
| `verification.stable_after` | Burn-in window before incident close |
| `experiment` (optional) | Paths to `plan.json` / `schedule.csv`; planned units + freeze gates |
| `runtime_loader` (optional) | Plugin name or `module:callable` factory |

When `experiment.plan` is set, the harness loads `experiment/plan.json` and uses `planned_units` for completion/status. Classify `plan_hash` / `design_seed` as `research_semantic` in `fingerprint_field_classifications()`.

## Wiring into the harness

### Register your runtime (recommended)

Projects register a factory via Python entry points. The harness discovers them; your repo only provides adapters.

**In your project's `pyproject.toml`:**

```toml
[project.entry-points."research_harness.runtimes"]
policy-simulation-eval = "policy_eval.harness:create_runtime"
```

**Factory signature:**

```python
def create_runtime(*, project_id: str, state_dir: Path, **options) -> RuntimeAdapter:
    return PolicySimulationHarness(project_id=project_id, state_dir=state_dir)
```

**In `contract.yaml` (optional — avoids repeating CLI flags):**

```yaml
runtime_loader:
  plugin: policy-simulation-eval
  # or explicit:
  # entrypoint: policy_eval.harness:create_runtime
  options: {}
```

**CLI usage:**

```bash
research-harness runtimes list
research-harness --runtime policy-simulation-eval status
research-harness --runtime-entrypoint policy_eval.harness:create_runtime preflight full_relaunch
research-harness run --runtime policy-simulation-eval --max-ticks 20
```

Resolution order: `--runtime-entrypoint` → `contract.runtime_loader` → `--runtime` → auto-detect (`file` / `failing-worker`).

### Compose modules explicitly (advanced)

You can also compose modules explicitly:

```python
from research_harness.reconciliation import Reconciler
from research_harness.incidents import IncidentEngine
from research_harness.contract.loader import load_contract
from research_harness.ledger import LedgerStore

contract = load_contract("contract.yaml")
runtime = MyProjectRuntime(...)
ledger = LedgerStore(".research-harness/ledger.db")

reconciler = Reconciler(contract=contract, runtime=runtime, ledger=ledger)
reconciler.reconcile(desired_fingerprint=desired)

engine = IncidentEngine(contract=contract, runtime=runtime, checkpoint=checkpoint, ...)
engine.evaluate(observed=runtime.inspect(), progress=..., desired_fingerprint=desired)
```

Prefer the supervisor loop for drop-in use: `research-harness run`.

## Checklist for a new repo

- [ ] Install Agent Skills from this repo (`research-harness`, `research-harness-adapter`; optional `research-harness-plan`)
- [ ] `research-harness init` (optionally `--with-experiment`)
- [ ] `contract.yaml` with project-specific fingerprint fields and authority bounds
- [ ] Optional `experiment/plan.json` frozen before outcomes
- [ ] `RuntimeAdapter` reading **applied** runtime state (not file edits alone)
- [ ] `CheckpointAdapter` if workers are resumable
- [ ] `DiagnosticsAdapter` for incident evidence
- [ ] Progress watermarks exposed (completed units + heartbeat)
- [ ] Register runtime entry point / `runtime_loader`
- [ ] `promote --from repository` then `run`
- [ ] Acceptance: crash recovery, stall recovery, config swap, negative result recording
- [ ] Run harness against `failing_worker` patterns before production workloads

## What not to put in adapters

- LLM provider SDK calls in harness core
- Hypothesis evaluation logic (that's your research code)
- Sealed prompts, credentials, or unpublished evaluators (keep private; adapter returns only operational signals)
- Domain package skills (Scanpy, RDKit, …) — install those in the host project separately

See [SPEC.md](SPEC.md) OSS strategy table for public vs private boundaries.
