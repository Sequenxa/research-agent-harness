# Contract overview (v1.1)

Minimal shape for drop-in projects:

```yaml
contract_version: 1
project:
  id: my-project
  objective: Determine whether X affects Y.

fingerprint:
  fields: [git_sha, config_hash, model, plan_hash, design_seed]
  on_change:
    config_hash: worker_restart
    model: full_relaunch
    plan_hash: full_relaunch
    design_seed: full_relaunch
  default: full_relaunch

progress:
  watermarks:
    - name: completed_units
      source: ledger
      stall_after: 20m
    - name: worker_heartbeat
      source: adapter
      stall_after: 90s
  stall_requires: any

validity:
  expected_units: 1000   # overridden by experiment/plan.json planned_units when present
  max_null_rate: 0.02
  max_error_rate: 0.05
  require_fingerprint_match: true
  on_invalid: quarantine

authority:
  runtime_restarts: true
  architecture_changes: false

verification:
  smoke_test: required
  stable_after:
    units: 5
    min_duration: 10m
    no_recurrence_within: 30m

completion:
  condition: units_completed >= 1000 and validity.passed
  on_complete: [snapshot_ledger, stop_workers]

escalation:
  channel: file
  blocking_timeout: 24h
  on_timeout: stop

# Optional methodology seam
experiment:
  plan: experiment/plan.json
  schedule: experiment/schedule.csv

# Optional runtime discovery
runtime_loader:
  plugin: my-project
  # or entrypoint: my_pkg.harness:create_runtime
```

`plan_hash` / `design_seed` are **research_semantic** fingerprint fields: changing the frozen design is a scientific change, not a silent hot reload.
