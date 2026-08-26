# Why Not Temporal / Prefect / Airflow?

The harness is **not** a replacement for durable workflow orchestration. It solves a different problem: **research execution that must be provably working**, not merely **scheduled or retried**.

## What orchestrators do well

Temporal, Prefect, Airflow, and Kubernetes operators excel at:

- Durable execution and task graphs
- Retries with backoff
- Scheduling and dependency management
- Infrastructure-level failure recovery (pod restart, task re-queue)

If your problem is "run step B after step A across days with retries," use an orchestrator.

## What they do not guarantee

A workflow platform can report **SUCCESS** when:

- The worker process exited cleanly but wrote no results
- The wrong model version is still loaded in memory
- Progress flatlined for an hour but the heartbeat task ran
- A metric near zero is recorded as a scientific finding when shards failed to load

These are **semantic** failures — the system is "up" but the experiment is not actually progressing or producing valid data.

## What this harness adds

| Concern | Harness behavior |
|---------|------------------|
| **Desired vs observed** | Continuous reconciliation; stale fingerprint triggers relaunch |
| **Progress ≠ health** | Watchdog detects healthy-but-stalled workloads |
| **Verify to stable** | Incidents close only after burn-in (units **and** duration) |
| **Validity gates** | Distinguish operational failure from scientific negative results |
| **Recovery budgets** | No infinite identical fixes; oscillation detection |
| **Contract authority** | Scoped remediation; block when boundary exceeded |

## When to use both

```
Orchestrator          Harness
     │                    │
     ▼                    ▼
  Task graph         Experiment contract
  Retry policy       Fingerprint + watchdog
  Schedule           Incident + recovery
                     Verify → stable
```

A sensible split:

- **Orchestrator** — DAG structure, task dispatch, infra retries
- **Harness** — whether the research workload is actually running the intended configuration, advancing, and producing valid measurable output

## Comparison to "AI scientist" frameworks

Inspect AI, Autolab, CORAL, and similar tools focus on **building and running research agents**. This harness focuses on **keeping a declared experiment in a reconciled working state** after changes, crashes, and config swaps — without conflating patch application with completion.

## Bottom line

Choose Temporal/Prefect when you need **workflow durability**.

Choose this harness when you need **semantic progress**, **verify-to-stable**, and **operational vs scientific failure separation** on top of (or beside) your execution layer.

For integration steps, see [ADAPTERS.md](ADAPTERS.md).
