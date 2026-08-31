# Research Agent Harness

> **Research software should not stop because an agent applied a patch.** This harness supervises experiments until they are actually working again.

A reconciliation controller for research execution — not another generic autonomous researcher.

## Status

**v0.2 dogfood milestone** — core architecture validated by real project integration (`policy-simulation-eval`), not just synthetic acceptance tests.

| Capability | Status |
|------------|--------|
| Deterministic acceptance suite (A–F) | ✓ |
| Real project `RuntimeAdapter` + Docker inspection | ✓ |
| Three-way deployment fingerprinting (running / desired / repository) | ✓ |
| Stale live runtime detection + graded relaunch | ✓ |
| Mutation preflight + autonomous prerequisite remediation | ✓ |
| Post-relaunch fingerprint verification | ✓ |
| Runtime plugins via entry points / contract hook | ✓ |
| Supervisor loop (`research-harness run`) | ✓ |
| Optional experiment plan artifacts + Agent Skills drop-in | ✓ |

Not yet proven: live worker resume after reconciliation in a running scheduler (offline canary only so far).

| Doc | Purpose |
|-----|---------|
| [docs/SPEC.md](docs/SPEC.md) | Full 25-section specification |
| [docs/ADAPTERS.md](docs/ADAPTERS.md) | **Integrate your repo** — implement Runtime/Checkpoint/Diagnostics adapters |
| [docs/WHY_NOT_ORCHESTRATORS.md](docs/WHY_NOT_ORCHESTRATORS.md) | vs Temporal / Prefect / Airflow |
| [docs/MILESTONE.md](docs/MILESTONE.md) | v0.1 + v0.2 dogfood report, limitations, next steps |

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
# Required on external volumes (macOS AppleDouble ._ sidecars break .venv on the drive)
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv
uv sync --dev

# End-to-end example (init → units → config swap → reconcile)
uv run python examples/failing_worker/run.py demo

# Harness CLI
uv run research-harness init --id demo --objective "Determine whether X affects Y."
# Optional methodology scaffold:
# uv run research-harness init --id demo --objective "..." --with-experiment --planned-units 4 --force
uv run research-harness validate
uv run research-harness status
uv run research-harness run --runtime failing-worker --max-ticks 5
```

If `uv sync` / `uv run` fails with `._ruff` / RECORD mismatches, remove the on-drive venv and use the export above:

```bash
rm -rf .venv
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv
uv sync --dev
```

## Integrating an external repo

Drop-in path (Agent Skills + adapters):

1. Install skills from this repo (`plugin.json` + `skills/`):
   ```bash
   npx skills add <owner>/research-agent-harness
   # or: gh skill install <owner>/research-agent-harness research-harness
   ```
2. `uv run research-harness init --id my-project --objective "..."`  
   Optional methodology seam: add `--with-experiment --planned-units 4`
3. Implement adapters (skill `research-harness-adapter` or [docs/ADAPTERS.md](docs/ADAPTERS.md))
4. Register runtime via `runtime_loader` / entry points
5. `research-harness promote --from repository` then `research-harness run`

Optional: host projects can install K-Dense [scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills) (`experimental-design`, `hypothesis-generation`, …) and project outputs into `experiment/plan.json` via skill `research-harness-plan`. Domain skills stay in the host — not in harness core.

Use `FailingWorkerRuntime` and acceptance tests A–F as templates.

## Project layout

```text
research-agent-harness/
├── plugin.json             # Agent Plugins manifest
├── skills/                 # drop-in agent skills
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

Apache-2.0 — see [LICENSE](LICENSE).
