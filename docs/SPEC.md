# Research Agent Harness — Specification Reference

> **Core thesis:** A code/configuration change is not completion. Completion means the intended system is live, observable, progressing, measured, and stable.

This document records the full 25-section specification as a reference. Implementation proceeds **phase by phase** — do not scaffold everything at once.

---

## OSS Strategy (Pre-Implementation Notes)

**Build open-source-ready; keep repo private during first dogfood cycle.**

| Public (eventually) | Private (always) |
|---|---|
| Research harness | Actual research datasets |
| Supervisor/reconciler | Hidden evaluators |
| Watchdog | Sealed prompts |
| Incident engine | Credentials |
| Adapter interfaces | Unpublished hypotheses |
| Run ledger | Project-specific research methods |
| Project contract schema | Sequenxa-specific infrastructure |
| Runtime fingerprinting | |
| Health/progress framework | |
| CLI | |
| Example projects | |

**License:** Apache-2.0 when published.

**Release sequence:**
- v0.1 — deterministic harness (fake worker)
- v0.2 — `policy-simulation-eval` adapter
- v0.3 — `model-collapse-research` adapter
- v0.4 — remove Sequenxa-specific assumptions (prefer: keep core clean from day one; Sequenxa adapters live in private repos)
- v0.5 — public release

**Do not integrate into existing Sequenxa repos until the harness proves itself independently.**

**Competitive framing:** Compare to durable execution (Temporal, Prefect, Airflow, K8s operators), not AI-scientist frameworks. Those handle resume/idempotency; this harness adds **semantic progress** and **verify-to-stable**.

---

## Section 1 — Operating Philosophy

Treat the harness as a **reconciliation controller**.

Every project has:
1. **Desired state**
2. **Observed state**
3. **Hard research invariants**
4. **Operational authority**
5. **Success metrics**
6. **Progress metrics**
7. **Recovery boundaries**

The supervisor continuously compares desired state with observed state. If they differ, reconcile them. Do not stop merely because a source-code or configuration change was made.

**Example flow (model swap):**
```text
User requests model swap
→ modify configuration
→ determine affected runtime
→ checkpoint work if required
→ rebuild/restart affected components
→ verify new model is actually running
→ execute smoke test
→ resume experiment
→ observe progress
→ pass stability window
→ declare reconciliation successful
```

---

## Section 2 — Keep Governance Lightweight

Four classes of project information (not university-style governance):

| Class | Description | Authority |
|---|---|---|
| **Hard invariant** | Cannot change automatically; would invalidate experiment or violate safety/data boundary | Highest |
| **Objective** | What the research project is trying to determine | High |
| **Current decision** | Current implementation choice; may change when contract grants authority | Medium |
| **Historical record** | What happened previously; no authority over current execution | None |

**Precedence:**
```text
hard invariants
→ research objective
→ project contract
→ current runtime configuration
→ historical decisions
```

ADRs and historical decisions must never silently override a current project contract.

---

## Section 3 — Project Contract

Machine-readable YAML with validated schema (Pydantic). Projects customize values; harness does not hard-code defaults from examples.

See [Enhanced Contract Schema](#enhanced-contract-schema-v11) at end of document for the refined shape incorporating validity checks, hierarchical progress, scoped authority, and recovery budgets.

**Original minimal shape (superseded in part by enhanced schema):**
```yaml
project:
  name: example
  objective: Determine whether X affects Y.

runtime:
  mode: continuous
  auto_relaunch: true
  auto_recover: true
  resume_from_checkpoint: true

authority:
  code_changes: true
  config_changes: true
  dependency_changes: true
  runtime_restarts: true
  model_swaps: true
  provider_swaps: true
  architecture_changes: true

limits:
  max_spend_usd: 100
  destructive_operations_require_approval: true
  publication_requires_approval: true

progress:
  metric: completed_units
  stall_after_seconds: 1200

verification:
  smoke_test_required: true
  stable_after_successful_units: 5

recovery:
  max_identical_fix_attempts: 2
  allow_alternative_solutions: true
```

---

## Section 4 — Core State Machine

### Original (single enum — has overlap issues)
```text
INITIALIZING | READY | RUNNING | DEGRADED | STALLED | RECOVERING
VERIFYING | STABLE | BLOCKED | COMPLETED | STOPPED
```

### Recommended refinement (orthogonal dimensions)
Model as **lifecycle** plus independent flags rather than cramming health/progress/incident into one axis:

**Lifecycle:** `INITIALIZING` → `RUNNING` → `BLOCKED` | `COMPLETED` | `STOPPED`

**Independent dimensions (flags, not states):**
- `runtime_health`: HEALTHY | UNHEALTHY — workload functioning
- `progress`: ADVANCING | STALLED
- `runtime_freshness`: CURRENT | STALE — running vs **desired deployment** (not repository HEAD)
- `inspection`: AVAILABLE | UNAVAILABLE — harness can observe state
- `mutation_readiness`: READY | REPAIRABLE | BLOCKED — project preflight for contemplated action
- `incident_status`: NONE | OPEN | RECOVERING | VERIFYING
- `verification_level`: PATCHED | VERIFIED | STABLE

Observe/diagnose/plan are permitted when inspection works. Mutate/relaunch additionally requires `mutation_preflight(action)` from the project adapter. When preflight returns **REPAIRABLE**, the harness applies listed prerequisite repairs (with verification), re-runs preflight, then mutates if READY.

Transitions must be recorded. A process being alive does not imply RUNNING or STABLE.

---

## Section 5 — Desired State vs Observed State

Implement:
```python
DesiredState
ObservedState
ReconciliationResult
```

**Observed information examples:**
- process/container state
- git SHA, configuration hash, dependency lock hash
- model, provider
- dataset version, prompt version, evaluator version
- last completed unit, last checkpoint, last successful result, last error
- spend

Provide extension points for project-specific observed-state fields.

---

## Section 6 — Runtime Fingerprint

Deterministic runtime fingerprint including (configurable per project):
```text
git_sha | dependency_lock_hash | container/image_id | model | provider
prompt_version | dataset_version | experiment_config_hash | schema_version | evaluator_version
```

Projects add custom fields. Harness detects `desired_deployment_fingerprint != running_fingerprint` and classifies runtime as **stale**. Stale runtime triggers reconciliation when authority permits **and** `mutation_preflight(action)` passes.

**Three fingerprints (do not conflate):**
- `repository_fingerprint` — current source tree / manifest (e.g. Git HEAD)
- `desired_deployment_fingerprint` — explicit deployment intent (`desired_fingerprint.json` or `promote`)
- `running_fingerprint` — what the live workload is using

Repository ahead of desired is normal during development; it does not imply relaunch until desired deployment is promoted.

Fingerprint → relaunch action mapping may live in contract (project policy) with adapter-supplied defaults.

---

## Section 7 — Progress Watchdog

**Health and progress are separate concepts.**

```python
HealthCheck   # process exists, container responds, DB reachable, heartbeat current
ProgressCheck # completed units increasing, checkpoint advancing, eval count increasing
```

A system may be **healthy but stalled**.

If progress watermark does not advance within stall window: treat as stalled and open incident.

### Hierarchical progress (design refinement)
Single `stall_after_seconds` is insufficient for real workloads. Support:
- **Coarse watermark** (e.g. completed_units) + **fine-grained heartbeat**
- **Per-phase stall windows** (dataset_load, warmup, etc.)
- **Slow-operation grace** — workload declares "expect nothing for N minutes"
- **`stall_requires`:** `any` | `all` watermarks stalled before declaring stall

False-positive stalls are expensive (kill healthy work, pay to redo).

---

## Section 8 — Incident and Recovery Engine

**Incident lifecycle:**
```text
DETECT → DIAGNOSE → HYPOTHESIZE → REMEDIATE → RELAUNCH → VERIFY → BURN_IN → RESUME → CLOSE
```

**Incident record contains:**
- incident ID, timestamp
- symptoms, evidence, suspected causes
- attempted remediations, verification results, final resolution

**Evidence is not LLM explanation.** Logs, exit codes, state files, API responses, metrics, process state, and project-defined diagnostics are evidence.

---

## Section 9 — Recovery Behavior

Authorized failures remediated automatically (via adapters):
- crashed worker, stale runtime, failed process, bad checkpoint pointer
- transient provider failure, invalid model response, scheduler stall
- container needing rebuild, configuration not applied, dependency/runtime mismatch

**Do not repeatedly apply the same unsuccessful fix.** Track remediation fingerprints.

If `max_identical_attempts` exceeded → select different strategy. Supervisor reasons from new evidence.

### Recovery budget (design refinement)
Beyond attempt counter:
- **Oscillation detection** (A→B→A→B counts as no progress)
- Per-incident ceilings: wall-clock, remediation count, spend
- `BLOCKED` reachable via budget exhaustion, not only contract boundary
- `novel_strategy_requires: evidence_delta` — no creative flailing without new observations

---

## Section 10 — Verification and Burn-In

Three levels:
```text
PATCHED  — remediation/change successfully applied
VERIFIED — immediate intended behavior observed
STABLE   — real workload continued successfully for stability window
```

Incident closes only after STABLE (unless project configures otherwise).

**Stability window:** require both unit count AND minimum duration (AND, not OR). Five fast units ≠ burn-in.

---

## Section 11 — Relaunch Semantics

Fingerprint changes may require (adapter/contract declared):
```text
no_action | hot_reload | worker_restart | service_restart | container_rebuild | full_runtime_relaunch
```

Do not assume editing a config file updates the running system. Always verify observed state after reconciliation.

---

## Section 12 — Research Result vs Operational Failure

| Scientific outcome (valid data) | Operational failure (incident) |
|---|---|
| hypothesis unsupported | scheduler stopped |
| negative result | wrong model ran |
| metric worsened | data stopped saving |
| effect size near zero | evaluator failed |
| | worker hung, checkpoint didn't advance |

Never treat operational failure as scientific result.

### Validity checks (design refinement — first-class concept)
Dangerous cases look like scientific outcomes but are operational:
- metric near zero because shards failed to load
- evaluator version drifted
- provider returning degraded-but-valid JSON

**Validity** gates results into the ledger. Failing validity opens incident even if nothing crashed. Some validity failures (`on_fail: block`) must not auto-remediate possible real signals.

---

## Section 13 — Swappable Architecture

### v0.1 adapter scope (trimmed from original nine)
Ship first:
```python
RuntimeAdapter
CheckpointAdapter
DiagnosticsAdapter
```

Let others emerge from real use:
```python
ExperimentAdapter | ModelAdapter | ProviderAdapter | StorageAdapter
SchedulerAdapter | EvaluatorAdapter
```

Provide simple local adapters and fake adapters for testing. Do not implement dozens of providers in v0.1.

---

## Section 14 — Supervisor

One main supervisor (not a swarm of permanent agents). Owns reconciliation loop; invokes specialized reasoning only when useful.

```python
while not terminal:
    desired = load_desired_state()
    observed = inspect_runtime()

    incident = watchdog.evaluate(desired, observed)
    if incident:
        diagnose(); remediate(); reconcile_runtime(); verify(); continue

    differences = compare(desired, observed)
    if differences:
        reconcile(differences); verify(); continue

    verify_progress()
    if completion_condition_met():
        mark_completed(); break
```

Avoid uncontrolled busy loops.

### Supervisor crash safety (design refinement)
- Write remediation **intent record before execution**
- Startup pass reconciles orphaned intents
- **Lease/lockfile per project.id** — one supervisor per project
- Ledger event schema and fingerprint definition are irreversible design decisions — invest here

---

## Section 15 — Human Escalation

Escalate only at explicit project boundaries:
- spend ceiling would be exceeded
- destructive action requiring approval
- publishing externally
- sealed/private data boundary
- research objective would fundamentally change
- no authorized remediation remains

**Do not ask for approval** for: service restart, allowed code change, dependency replace, experiment resume, authorized model/provider swap, failed architectural choice retry.

**On timeout:** blocked runs need `escalation.on_timeout` behavior (e.g. stop workers after 24h).

---

## Section 16 — Run Ledger

Append-oriented event ledger (SQLite + JSON where appropriate).

**Events:** state transition, desired-state change, runtime reconciliation, experiment start/stop, checkpoint, incident, diagnosis, remediation, verification, model/provider swap, budget event, completion.

Every event records `contract_version`. Markdown is for human docs, not runtime state.

---

## Section 17 — CLI

```bash
research-harness init
research-harness validate
research-harness status
research-harness run
research-harness reconcile
research-harness inspect
research-harness incidents
research-harness doctor
research-harness stop
```

**`status` output example:**
```text
Project: example
State: RUNNING
Runtime health: HEALTHY
Progress: ADVANCING
Desired fingerprint: abc123
Observed fingerprint: abc123
Last progress: 38s ago
Last checkpoint: 41s ago
Current incident: none
Completed: 372 / 1000
```

---

## Section 18 — Repository Structure

```text
research-agent-harness/
├── AGENTS.md
├── README.md
├── LICENSE
├── pyproject.toml
├── src/
│   └── research_harness/
│       ├── cli.py
│       ├── contract/
│       ├── supervisor/
│       ├── reconciliation/
│       ├── runtime/
│       ├── watchdog/
│       ├── incidents/
│       ├── recovery/
│       ├── verification/
│       ├── ledger/
│       ├── adapters/
│       └── models/
├── tests/
├── examples/
│   ├── simulated_counter/
│   └── failing_worker/
└── docs/
```

Keep `AGENTS.md` short — philosophy and dev rules, not a decision register.

---

## Section 19 — Technology

| Tool | Purpose |
|---|---|
| Python 3.13 | Runtime |
| uv | Package management |
| pytest | Testing |
| ruff | Linting |
| mypy | Type checking |
| Pydantic | Contracts/state models |
| Typer | CLI |
| sqlite3 (stdlib) | Ledger/state |

**Avoid in v0.1:** web UI, Kubernetes, Redis, message broker, cloud dependency, provider SDK in core. Must work locally.

---

## Section 20 — First Example Project

Deterministic fake research workload (`failing_worker`):
1. Process numbered experiment units
2. Persist checkpoints
3. Expose progress watermark
4. Occasionally fail intentionally
5. Occasionally stall intentionally
6. Support configuration swap
7. Require restart for swap to take effect
8. Allow resume from checkpoint

**Proves harness can:** detect crash → recover → resume; detect stall → recover; detect stale runtime after config change → relaunch → verify → stability window → completion.

No LLM API required.

**Optional inversion (design note):** run harness in observe-only mode against real project before fake worker, to derive failure taxonomy from actual incidents.

---

## Section 21 — Acceptance Tests

Repository incomplete until automated tests demonstrate:

| Scenario | Behavior |
|---|---|
| **A: Crash recovery** | worker crashes → detect → incident → recovery → restart → checkpoint resume → progress → close after stability |
| **B: Alive but stalled** | worker alive, watermark stops → STALLED → recovery → progress resumes |
| **C: Runtime swap** | config A→B → fingerprint stale → relaunch → observed matches B → progress under B |
| **D: Bad fix** | strategy A fails twice → no infinite A → different strategy → evidence records attempts |
| **E: Unauthorized boundary** | recovery prohibited by contract → BLOCKED with exact boundary explanation |
| **F: Scientific negative result** | metric contradicts hypothesis but ops OK → recorded, no incident |

---

## Section 22 — Development Sequence

### Phase 1 — Scaffold
- Repository scaffold
- Project contract schema (enhanced)
- State models
- SQLite ledger
- CLI skeleton

### Phase 2 — Reconciliation
- Desired/observed state
- Runtime fingerprint
- Reconciliation engine

### Phase 3 — Watchdog
- Health checks
- Progress watermark (hierarchical)
- Watchdog
- Incident records
- Validity checks (basic)

### Phase 4 — Recovery
- Recovery strategies
- Verification
- Stability/burn-in logic
- Remediation intent + crash safety
- Project lease

### Phase 5 — Proof
- Deterministic failing-worker example
- Acceptance tests (A–F)

### Phase 6 — Documentation
- How external repos implement adapters
- Why not Temporal/Prefect

**Per-slice guidance:** Start with Phases 1–3 + acceptance tests A and B, then re-scope.

---

## Section 23 — Design Constraints

- Simple composable modules
- No abstractions without current use case
- No unnecessary governance documents or ADR framework
- No multi-agent bureaucracy
- Feature complete ≠ one function's unit tests pass
- Verify end-to-end
- Replace wrong choices rather than preserving them
- Optimize for: **working, observable, recoverable, replaceable, measurable, testable**

---

## Section 24 — Future Compatibility

Design core for later attachment (do not implement yet):
- Inspect AI, Concordia
- Arbitrary Python experiments
- Docker workloads, local/hosted models
- Scheduled research campaigns

First milestone: reliable generic control loop.

---

## Section 25 — Deliverable (First Milestone)

Implement complete first milestone, then report:
```text
what was built
tests executed
end-to-end behavior observed
known limitations
next recommended integration point
```

Do not stop at scaffolding if executable deterministic harness has not been demonstrated.

---

## Enhanced Contract Schema (v1.1)

Incorporates validity, hierarchical progress, scoped authority, recovery budgets, and executable invariants.

```yaml
contract_version: 1              # recorded in every ledger event

project:
  id: policy-simulation-eval     # also the lease key — one supervisor per id
  objective: Determine whether X affects Y.

# highest precedence, executable — each has a check every loop
invariants:
  - id: sealed-labels
    statement: Producer agents cannot read evaluator labels.
    check: fs_acl.sealed_paths_unreadable_by_worker
    on_violation: halt           # halt | block | incident

fingerprint:
  fields: [git_sha, lock_hash, model, provider, prompt_version,
           dataset_version, evaluator_version, config_hash]
  on_change:
    prompt_version: worker_restart
    config_hash: worker_restart
    lock_hash: rebuild
    model: full_relaunch
    git_sha: full_relaunch
  default: full_relaunch

progress:
  watermarks:
    - name: completed_units
      source: ledger
      stall_after: 20m
    - name: worker_heartbeat
      source: adapter
      stall_after: 90s
  phases:
    dataset_load: {stall_after: 45m}
    warmup:       {stall_after: 15m}
  slow_operation_grace: 60m
  stall_requires: any            # any | all

validity:
  expected_units: 1000
  max_null_rate: 0.02
  max_error_rate: 0.05
  require_fingerprint_match: true
  checks:
    - id: shard_count
      adapter: dataset
      on_fail: incident
    - id: score_distribution_drift
      adapter: evaluator
      on_fail: block             # never auto-remediate possible real signal
  on_invalid: quarantine         # quarantine | discard | incident

authority:
  code_changes:
    allow: ["src/workers/**", "configs/**"]
    deny:  ["src/evaluator/**", "data/**"]
  dependency_changes: patch_only # none | patch_only | minor | any
  model_swaps:
    allow: [gpt-4o-mini, claude-haiku-4-5]
  provider_swaps: true
  runtime_restarts: true
  architecture_changes: false

budget:
  total_usd: 100
  per_hour_usd: 15
  per_incident_usd: 5
  per_incident_wallclock: 30m
  warn_at: 0.7

recovery:
  max_identical_attempts: 2
  max_attempts_per_incident: 6
  detect_oscillation: true
  oscillation_window: 4
  backoff: exponential
  min_backoff: 30s
  novel_strategy_requires: evidence_delta

verification:
  smoke_test: required
  stable_after:
    units: 5
    min_duration: 10m            # AND, not OR
    no_recurrence_within: 30m

completion:
  condition: "units_completed >= 1000 and validity.passed"
  on_complete: [snapshot_ledger, stop_workers]

escalation:
  channel: file
  blocking_timeout: 24h
  on_timeout: stop
```

### Key schema decisions

| Field | Rationale |
|---|---|
| `invariants[].check` | Invariant without executable check is a comment |
| `on_violation: halt` | Sealed-data breach shouldn't wait for human while workers run |
| Scoped `authority` | Boolean flags grant unbounded rewrite authority |
| `validity.on_fail: block` | Don't auto-fix possible real scientific signals |
| `stable_after.min_duration` | Fast units alone don't constitute burn-in |
| `novel_strategy_requires: evidence_delta` | Prevents remediation flailing |
| `escalation.on_timeout` | Blocked runs shouldn't burn budget indefinitely |
| `project.id` as lease key | One supervisor per project |

---

## Open Design Questions

1. **Pydantic models first** vs **fingerprint/adapter split first** — both needed in Phase 1
2. **Observe-only mode** against real project before fake worker — valuable but optional for v0.1
3. **Fingerprint→action mapping** in contract vs adapter defaults with contract override

---

## Implementation Slices (Suggested Order)

| Slice | Scope | Acceptance |
|---|---|---|
| **1** | Phase 1: scaffold, contract schema, state models, ledger, CLI skeleton | `validate`, `init` work |
| **2** | Phase 2: desired/observed, fingerprint, reconciliation | stale runtime detected |
| **3** | Phase 3: health/progress watchdog, incidents, validity basics | scenarios A, B |
| **4** | Phase 4: recovery, verification, burn-in, intent/lease | scenarios C, D, E |
| **5** | Phase 5: failing_worker example + scenario F | full test suite green |
| **6** | Phase 6: adapter docs, README pitch | external repo can implement adapters |
