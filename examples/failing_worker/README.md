# failing_worker

Deterministic fake research workload for harness development and demos.

## Prerequisites

From the **repository root**, with [uv](https://docs.astral.sh/uv/) installed:

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv  # external drive workaround
uv sync --dev
```

Use `uv run` for all commands — `python` and `research-harness` are not on your PATH unless you activate the venv or install globally.

## One-command demo

```bash
uv run python examples/failing_worker/run.py demo
```

This runs: `init` → process 10 units → stage config swap → harness reconcile.

## Step by step

```bash
# Initialize worker state + contract.yaml
uv run python examples/failing_worker/run.py init

# Process units
uv run python examples/failing_worker/run.py step --count 10

# Stage config swap (runtime becomes stale until reconcile)
uv run python examples/failing_worker/run.py swap-config --config-hash cfg-b

# Apply relaunch via harness (uses FailingWorkerRuntime, not generic file adapter)
uv run python examples/failing_worker/run.py reconcile

# Inspect worker state
uv run python examples/failing_worker/run.py status
```

## Main harness CLI

The top-level CLI also works via `uv run`:

```bash
uv run research-harness init --contract examples/failing_worker/contract.yaml --id failing-worker --force
uv run research-harness validate --contract examples/failing_worker/contract.yaml
```

For `failing_worker`, prefer `run.py reconcile` — it wires up `FailingWorkerRuntime` correctly. The generic `research-harness reconcile` command uses file-based fingerprint JSON and is for a different workflow.

## State files

Under `examples/failing_worker/.state/`:

| File | Purpose |
|------|---------|
| `config.json` | Worker config and applied fingerprint fields |
| `worker_state.json` | Runtime state (running, units, pending config) |
| `checkpoint.json` | Resume checkpoint |
| `ledger.db` | Reconciliation events |

`contract.yaml` lives at `examples/failing_worker/contract.yaml`.
