# Milestone v0.1 — Deliverable Report

First deterministic harness milestone per [SPEC.md](SPEC.md) Section 25.

## What was built

| Component | Location |
|-----------|----------|
| Contract schema v1.1 | `src/research_harness/contract/` |
| State models (lifecycle + orthogonal flags) | `src/research_harness/models/` |
| Append-only SQLite ledger | `src/research_harness/ledger/` |
| CLI (full v0.1 surface) | `src/research_harness/cli.py` |
| Fingerprint compare + reconciliation | `src/research_harness/reconciliation/`, `runtime/` |
| Watchdog (health vs progress, hierarchical stalls) | `src/research_harness/watchdog/` |
| Incident engine + SQLite store | `src/research_harness/incidents/` |
| Validity gates | `src/research_harness/validity/` |
| Recovery budgets + remediation intents | `src/research_harness/recovery/` |
| Verification burn-in (PATCHED → VERIFIED → STABLE) | `src/research_harness/verification/` |
| Supervisor loop + lease + escalation | `src/research_harness/supervisor/` |
| Invariants + completion evaluators | `src/research_harness/invariants/`, `completion/` |
| Scientific result recorder | `src/research_harness/results/` |
| `failing_worker` example + DiagnosticsAdapter | `examples/failing_worker/`, `adapters/failing_worker.py` |
| Adapter guide | [docs/ADAPTERS.md](ADAPTERS.md) |

## Tests executed

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv
uv sync --dev
uv run pytest -q
```

**45+ tests passing**, including acceptance scenarios A–F and supervisor CLI coverage:

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
uv run research-harness run --runtime failing-worker --max-ticks 5
uv run research-harness doctor --runtime failing-worker
uv run research-harness stop
```

Observed output path:

1. `init` — writes `contract.yaml` and worker state under `.state/`
2. `step` — processes units, updates checkpoint
3. `swap-config` — stages `cfg-b`; runtime fingerprint stays on applied `cfg-a` (stale)
4. `reconcile` — `worker_restart` applies pending config; observed `config_hash` becomes `cfg-b`
5. `run` — supervisor loop ticks with lease, orphan intent reconciliation, escalation, invariants

Ledger records reconciliation and experiment events in `.state/ledger.db`.

## Known limitations (v0.1)

- **Invariant checks** — built-in stubs only; project adapters supply real enforcement
- **Budget spend** — reconciliation actions record nominal spend; no provider USD metering
- **Generic `research-harness reconcile`** — file-based fingerprint JSON; use `failing_worker/run.py reconcile` for the example worker
- **No web UI, K8s, Redis** — local SQLite + file state only

## Spec completion status

All 25 SPEC sections have a v0.1 implementation or documented deferral. Remaining work before dogfood is integration testing in a real Sequenxa repo, not core harness gaps.

## Next recommended integration point

Per [SPEC.md](SPEC.md) release sequence:

1. **v0.2** — `policy-simulation-eval` adapter in a real Sequenxa repo (private), implementing the three v0.1 adapters against an actual workload
2. **Observe-only mode** — run harness read-only against a live project to derive failure taxonomy before enabling recovery

Start with [ADAPTERS.md](ADAPTERS.md) and copy patterns from `FailingWorkerRuntime`.
