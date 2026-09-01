---
name: research-harness-adapter
description: Implement RuntimeAdapter, CheckpointAdapter, and DiagnosticsAdapter so a host research repo can run under the research harness. Use when wiring inspect/restart/relaunch/stop, mutation preflight, fingerprints, checkpoints, or diagnostics evidence collection for research-harness.
license: Apache-2.0
compatibility: Requires Python 3.13+ and research-harness package importable in the host project.
metadata:
  version: "1.0"
---

# Research Harness Adapter

Host projects supply thin adapters. Harness core stays provider- and science-agnostic.

## When to use

- Integrating an external research repo with the harness
- Implementing `inspect`, `restart_worker`, `relaunch`, `stop`
- Adding `mutation_preflight` / prerequisite repairs
- Exposing checkpoints or diagnostic evidence

## Factory contract

```python
from pathlib import Path
from research_harness.adapters.base import RuntimeAdapter

def create_runtime(*, project_id: str, state_dir: Path, **options) -> RuntimeAdapter:
    return MyProjectRuntime(project_id=project_id, state_dir=state_dir)
```

Register via entry point:

```toml
[project.entry-points."research_harness.runtimes"]
my-project = "my_pkg.harness:create_runtime"
```

Or in `contract.yaml`:

```yaml
runtime_loader:
  plugin: my-project
  # or entrypoint: my_pkg.harness:create_runtime
```

## Stub generator

From this skill directory:

```bash
python3 scripts/emit_adapter_stub.py --package my_pkg --class-name MyProjectRuntime -o my_pkg/harness.py
```

## Required RuntimeAdapter behavior

1. **`inspect()`** — report **applied** runtime fingerprint, not pending file edits.
2. **Three fingerprints** — running (inspect), desired (harness state), repository (`repository_fingerprint()` optional).
3. **`stop()`** — after stop, `inspect().lifecycle` must be `STOPPED`.
4. **`mutation_preflight(action)`** — return READY / REPAIRABLE / BLOCKED.
5. **`fingerprint_field_classifications()`** — `deployment` | `research_semantic` | `authorization_sensitive`.

Classify `plan_hash` and `design_seed` as `research_semantic` when using experiment plans.

## Orthogonal signals

| Signal | Question |
|--------|----------|
| runtime health | Is the workload functioning? |
| progress | Is work advancing? |
| runtime freshness | running == desired? |
| inspection | Can the harness observe state? |
| mutation readiness | Safe to mutate now? |
| scheduled_path_armed | Is the scheduled path loaded/armed? |

Never collapse native scheduler health into harness inspection health.

**Progress timestamps must be durable.** Do not set `last_progress_at = observed_at` (or watermark `last_advanced_at` equal to inspect time) — the harness treats that as `suspect_progress` and opens an incident.

**Recovery budgets** (`max_identical_attempts`, oscillation) apply only to harness remediation strategies, not project driver loops. Drivers must hard-stop themselves; expose `scheduled_path_armed` so the harness can detect calendar-held-by-drain.

## Checkpoint + Diagnostics

Implement when workers are resumable / when incidents need log/exit evidence. See `references/adapter_checklist.md`.

## What not to put in adapters

- LLM provider SDK calls in harness core
- Hypothesis evaluation or auto-scoring
- Sealed prompts, credentials, unpublished evaluators

## Related

- Repo guide: `docs/ADAPTERS.md`
- Operate CLI: skill `research-harness`
- Plan artifacts: skill `research-harness-plan`
