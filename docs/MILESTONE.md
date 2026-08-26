# Milestone v0.1 — Deliverable Report

First deterministic harness milestone per [SPEC.md](SPEC.md) Section 25.

## What was built

| Component | Location |
|-----------|----------|
| Contract schema v1.1 | `src/research_harness/contract/` |
| State models (lifecycle + orthogonal flags) | `src/research_harness/models/` |
| Append-only SQLite ledger | `src/research_harness/ledger/` |
| CLI (`init`, `validate`, `status`, `reconcile`, `incidents`, …) | `src/research_harness/cli.py` |
| Fingerprint compare + reconciliation | `src/research_harness/reconciliation/`, `runtime/` |
| Watchdog (health vs progress, hierarchical stalls) | `src/research_harness/watchdog/` |
| Incident engine + SQLite store | `src/research_harness/incidents/` |
| Validity gates | `src/research_harness/validity/` |
| Recovery budgets + remediation intents | `src/research_harness/recovery/` |
| Verification burn-in (PATCHED → VERIFIED → STABLE) | `src/research_harness/verification/` |
| Project lease | `src/research_harness/supervisor/lease.py` |
| Scientific result recorder | `src/research_harness/results/` |
| `failing_worker` example | `examples/failing_worker/` |
| Adapter guide | [docs/ADAPTERS.md](ADAPTERS.md) |

## Tests executed

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv
uv sync --dev
uv run pytest -q
```

**40 tests passing**, including acceptance scenarios A–F:

| Scenario | Proves |
|----------|--------|
| A | Crash → incident → restart → checkpoint resume → stable close |
| B | Alive but stalled → recovery → stable close |
| C | Config swap → stale fingerprint → relaunch → progress |
| D | Bad fix → strategy escalation (no infinite retry) |
| E | Unauthorized recovery → BLOCKED with boundary message |
| F | Negative scientific result recorded, no incident |

## End-to-end behavior observed

```bash
uv run python examples/failing_worker/run.py demo
```

Observed output path:

1. `init` — writes `contract.yaml` and worker state under `.state/`
2. `step` — processes units, updates checkpoint
3. `swap-config` — stages `cfg-b`; runtime fingerprint stays on applied `cfg-a` (stale)
4. `reconcile` — `worker_restart` applies pending config; observed `config_hash` becomes `cfg-b`

Ledger records reconciliation events in `.state/ledger.db`.

## Known limitations (v0.1)

- **`research-harness run`** — supervisor loop not wired to CLI yet; compose `IncidentEngine` + `Reconciler` manually or via repo wrapper
- **`DiagnosticsAdapter`** — interface only; no bundled implementation beyond contract hooks
- **Generic `research-harness reconcile`** — file-based fingerprint JSON; use `failing_worker/run.py reconcile` for the example worker
- **Human escalation** — contract fields exist; file-channel escalation not implemented
- **Spend tracking** — budget fields in contract; per-incident USD not enforced at runtime
- **No web UI, K8s, Redis** — local SQLite + file state only

## Next recommended integration point

Per [SPEC.md](SPEC.md) release sequence:

1. **v0.2** — `policy-simulation-eval` adapter in a real Sequenxa repo (private), implementing the three v0.1 adapters against an actual workload
2. **Observe-only mode** — run harness read-only against a live project to derive failure taxonomy before enabling recovery
3. **Supervisor CLI** — wire lease + orphan intent reconciliation into `research-harness run`

Start with [ADAPTERS.md](ADAPTERS.md) and copy patterns from `FailingWorkerRuntime`.
