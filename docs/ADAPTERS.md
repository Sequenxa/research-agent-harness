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

| Preflight status | Meaning | Harness action |
|------------------|---------|----------------|
| `READY` | Can mutate now | Proceed |
| `REPAIRABLE` | Permitted prerequisite must be fixed first | Apply repairs, verify, re-run preflight |
| `BLOCKED` | Requires authority the harness does not have | Stop mutation and escalate |

Observe and diagnose are always permitted when inspection works. Plan remediation is permitted. **Mutate/relaunch** requires passing `mutation_preflight(action)` (after any REPAIRABLE remediation) in addition to harness authority.

```bash
research-harness status
research-harness preflight full_relaunch
research-harness preflight full_relaunch --repair
research-harness promote --from repository
```

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
uv run research-harness validate --contract ./contract.yaml
```

Key sections for adapters:

| Section | Adapter impact |
|---------|----------------|
| `fingerprint` | Which fields you must populate in `inspect()` |
| `progress.watermarks` | What stall detection expects (ledger vs adapter sources) |
| `authority` | What recovery actions are allowed |
| `recovery` | Budget limits, oscillation detection |
| `verification.stable_after` | Burn-in window before incident close |

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
```

Resolution order: `--runtime-entrypoint` → `contract.runtime_loader` → `--runtime` → auto-detect (`file` / `failing-worker`).

### Compose modules explicitly (advanced)

Today (v0.1), you can also compose modules explicitly:

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

A full supervisor loop (`research-harness run`) is planned; until then, drive evaluation from your repo's main loop or a thin wrapper script.

## Checklist for a new repo

- [ ] `contract.yaml` with project-specific fingerprint fields and authority bounds
- [ ] `RuntimeAdapter` reading **applied** runtime state (not file edits alone)
- [ ] `CheckpointAdapter` if workers are resumable
- [ ] `DiagnosticsAdapter` for incident evidence
- [ ] Progress watermarks exposed (completed units + heartbeat)
- [ ] Acceptance: crash recovery, stall recovery, config swap, negative result recording
- [ ] Run harness against `failing_worker` patterns before production workloads

## What not to put in adapters

- LLM provider SDK calls in harness core
- Hypothesis evaluation logic (that's your research code)
- Sealed prompts, credentials, or unpublished evaluators (keep private; adapter returns only operational signals)

See [SPEC.md](SPEC.md) OSS strategy table for public vs private boundaries.
