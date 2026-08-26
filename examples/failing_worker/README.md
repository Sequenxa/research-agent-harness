# failing_worker

Deterministic fake research workload for harness development and demos.

## What it does

- Processes numbered experiment units with checkpoints on disk
- Exposes progress watermarks for the watchdog
- Crashes at configured units (`crash_at_units`)
- Stalls at configured units (`stall_at_units`)
- Supports config swaps that require relaunch (`set_pending_config`)
- Produces a cumulative effect-size metric for scientific outcome recording

No LLM API required.

## Quick start

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv
uv sync --dev

# Initialize worker state
python examples/failing_worker/run.py init

# Process units
python examples/failing_worker/run.py step --count 5

# Stage a config swap (harness reconcile applies it)
python examples/failing_worker/run.py swap-config --config-hash cfg-b
research-harness reconcile --state-dir examples/failing_worker/.state
```

State files live under `examples/failing_worker/.state/`:
- `config.json` — worker configuration and fingerprint fields
- `worker_state.json` — runtime state
- `checkpoint.json` — resume checkpoint

## Harness integration

Use `FailingWorkerRuntime` from `research_harness.adapters.failing_worker` as the
`RuntimeAdapter` and `CheckpointAdapter` when driving this example from tests or the supervisor.
