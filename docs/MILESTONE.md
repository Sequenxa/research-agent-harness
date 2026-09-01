# Milestone Report

## v0.2 — Dogfood milestone (current)

Real-project validation in `policy-simulation-eval`. Core reconciliation flow is implemented in the harness `Reconciler`, not project-specific orchestration.

### What dogfood proved

The harness can:

1. Detect that a real research deployment is stale
2. Choose the required relaunch action
3. Refuse an initially unsafe mutation via preflight
4. Recognize an autonomously repairable prerequisite
5. Repair and verify the prerequisite
6. Sync repaired deployment/authorization fields into desired state
7. Relaunch and confirm the deployment converged to the promoted desired fingerprint

### Reconciliation flow (core)

```text
STALE
↓
choose strongest relaunch action
↓
mutation preflight
↓
REPAIRABLE
↓
repair prerequisite
↓
verify repair
↓
sync repaired deployment state
↓
preflight READY
↓
relaunch
↓
reinspect actual runtime
↓
require fingerprint CURRENT
```

### Key commits since v0.1

| Commit | Change |
|--------|--------|
| `2c2bdbc` | Leases, blocked-state, observe-only, budgets, stop safety, orphan intents |
| `c242930` | Running / desired / repository fingerprints, mutation preflight |
| `f6c18bc` | `REPAIRABLE`, prerequisite remediation, deployment-delta bundling |
| `e507103` | External runtime plugins via entry points + contract hook |
| `08b0b6e` | Reconcile via runtime loader; sync desired after repair |

### Tests

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv
uv sync --dev
uv run pytest -q
```

**74+ tests passing**, including acceptance scenarios A–F, supervisor safety, preflight remediation, and operational signal coverage.

### Known limitations (v0.2)

- **Live scheduler resume** — offline canary validated reconciliation; live `24 → 25` unit resume not yet observed
- **Invariant checks** — built-in stubs only; project adapters supply real enforcement
- **Budget spend** — reconciliation actions record nominal spend; no provider USD metering
- **No web UI, K8s, Redis** — local SQLite + file state only
- **Project driver loops** — harness recovery budgets do not auto-stop project-owned drains; adapters must report durable progress + `scheduled_path_armed`

### Next steps

1. Live v10 canary observing unit resume after reconciliation
2. Second adapter: `model-collapse-research` (different failure modes than policy simulation)
3. No new harness architecture until dogfood forces it

---

## v0.1 — Initial deterministic milestone

First deterministic harness milestone per [SPEC.md](SPEC.md) Section 25.

### What was built

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

### Acceptance scenarios

| Scenario | Proves |
|----------|--------|
| A | Crash → incident → restart → checkpoint resume → stable close |
| B | Alive but stalled → recovery → stable close |
| C | Config swap → stale fingerprint → relaunch → progress |
| D | Bad fix → strategy escalation (no infinite retry) |
| E | Unauthorized recovery → BLOCKED with boundary message |
| F | Negative scientific result recorded, no incident |
