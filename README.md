# Research Agent Harness

> **Research software should not stop because an agent applied a patch.** This harness supervises experiments until they are actually working again.

A reconciliation controller for research execution — not another generic autonomous researcher.

## Status

**v0.1 milestone complete** (Slices 1–6). Deterministic harness with contract validation, reconciliation, watchdog, incidents, recovery budgets, burn-in verification, and acceptance scenarios A–F.

| Doc | Purpose |
|-----|---------|
| [docs/SPEC.md](docs/SPEC.md) | Full 25-section specification |
| [docs/ADAPTERS.md](docs/ADAPTERS.md) | **Integrate your repo** — implement Runtime/Checkpoint/Diagnostics adapters |
| [docs/WHY_NOT_ORCHESTRATORS.md](docs/WHY_NOT_ORCHESTRATORS.md) | vs Temporal / Prefect / Airflow |
| [docs/MILESTONE.md](docs/MILESTONE.md) | What shipped in v0.1, limitations, next steps |

## Core principle

A code/configuration change is not completion. Completion means the intended system is live, observable, progressing, measured, and stable.

## What this is (and isn't)

| This harness | Not this |
|---|---|
| Outcome-oriented supervisor | Eval construction (Inspect AI) |
| Reconciles toward working state | Production workflow orchestration (Inspect Flow) |
| Diagnoses stalls, repairs failures | Autonomous research campaigns (Autolab) |
| Verifies patch → stable | Multi-agent autoresearch (CORAL) |

Compared to Temporal/Prefect/Airflow: those handle durable execution and retries. This adds **semantic progress detection**, **verify-to-stable**, and **operational vs scientific failure separation**.

## Quick start

Requires [uv](https://docs.astral.sh/uv/). From repo root:

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv  # external drive workaround
uv sync --dev

# End-to-end example (init → units → config swap → reconcile)
uv run python examples/failing_worker/run.py demo

# Harness CLI
uv run research-harness init
uv run research-harness validate
uv run research-harness status
```

## Integrating an external repo

1. Read [docs/ADAPTERS.md](docs/ADAPTERS.md)
2. Copy `contract.yaml` patterns from `uv run research-harness init`
3. Implement `RuntimeAdapter`, `CheckpointAdapter`, `DiagnosticsAdapter` in your repo
4. Use `FailingWorkerRuntime` and acceptance tests A–F as templates
5. Drive `Reconciler` + `IncidentEngine` from your supervisor loop until `research-harness run` lands

## Project layout

```text
research-agent-harness/
├── src/research_harness/   # core harness
├── examples/failing_worker/  # deterministic demo workload
├── tests/acceptance/       # scenarios A–F
└── docs/                   # spec, adapters, milestone
```

## Development

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Python 3.13. See [AGENTS.md](AGENTS.md) for agent/contributor rules.

## License

Apache-2.0 (planned for public release after dogfood cycle).
