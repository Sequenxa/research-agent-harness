# Adapter checklist

- [ ] `create_runtime(project_id, state_dir, **options) -> RuntimeAdapter`
- [ ] Entry point under `research_harness.runtimes` or `contract.runtime_loader`
- [ ] `inspect()` returns applied fingerprint + completed_units + heartbeats
- [ ] Progress timestamps are durable (never `last_progress_at = observed_at`)
- [ ] Optional `scheduled_path_armed` + operational mode for drain-hold detection
- [ ] `restart_worker` / `relaunch` / `stop` (+ verified STOPPED)
- [ ] `mutation_preflight` + optional `repair_mutation_prerequisite`
- [ ] Fingerprint field classifications declared
- [ ] Optional `repository_fingerprint()`
- [ ] CheckpointAdapter if resumable
- [ ] DiagnosticsAdapter for incident evidence
- [ ] Project drivers enforce their own identical-failure hard-stops (harness recovery budgets do not)
- [ ] Acceptance: crash recovery, stall, config swap, negative result recording
- [ ] Dogfood against `failing_worker` patterns before production workloads
