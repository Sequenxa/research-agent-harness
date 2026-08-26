# Research Agent Harness

> **Research software should not stop because an agent applied a patch.** This harness supervises experiments until they are actually working again.

A reconciliation controller for research execution — not another generic autonomous researcher.

## Status

**Slice 1–5 complete.** Phase 5 adds the `failing_worker` example, scientific result recording, and acceptance scenario F. See [docs/SPEC.md](docs/SPEC.md).

## Core Principle

A code/configuration change is not completion. Completion means the intended system is live, observable, progressing, measured, and stable.

## What This Is (and Isn't)

| This harness | Not this |
|---|---|
| Outcome-oriented supervisor | Eval construction (Inspect AI) |
| Reconciles toward working state | Production workflow orchestration (Inspect Flow) |
| Diagnoses stalls, repairs failures | Autonomous research campaigns (Autolab) |
| Verifies patch → stable | Multi-agent autoresearch (CORAL) |

Compared to Temporal/Prefect/Airflow: those handle durable execution and retries. This adds **semantic progress detection** and **verify-to-stable** — refusing to confuse "patch applied" with "system working."

## Planned Structure

```text
research-agent-harness/
├── src/research_harness/
│   ├── cli.py
│   ├── contract/
│   ├── supervisor/
│   ├── reconciliation/
│   ├── runtime/
│   ├── watchdog/
│   ├── incidents/
│   ├── recovery/
│   ├── verification/
│   ├── ledger/
│   ├── adapters/
│   └── models/
├── tests/
├── examples/
└── docs/
```

## License

Apache-2.0 (planned for public release after dogfood cycle).
