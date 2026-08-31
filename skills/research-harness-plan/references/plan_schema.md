# experiment/plan.json schema (1.0)

Harness-owned thin projection. Only fields the reconciliation loop acts on.

```json
{
  "schema_version": "1.0",
  "planned_units": 4,
  "design_seed": 42,
  "unit_of_analysis": "independent run cell",
  "frozen_before_outcomes": true,
  "schedule_path": "experiment/schedule.csv",
  "schedule_hash": "<sha256 of schedule file bytes>",
  "plan_hash": "<sha256 of canonical JSON without plan_hash>",
  "confirmatory_analyses": [
    {
      "analysis_id": "A1",
      "decision_rule": "Interpret compatibility with predicted patterns; do not select a hypothesis automatically."
    }
  ]
}
```

## Rules

- `schema_version` must be `"1.0"`.
- `planned_units` >= 1.
- `plan_hash` = SHA-256 of canonical JSON (`sort_keys=True`, separators `(',', ':')`) with `plan_hash` removed.
- `schedule_hash` optional; when set, harness compares against the live schedule file.
- `decision_rule` is human-readable text only — never used to auto-accept/reject science.
- Document the true replicate in `unit_of_analysis` (avoid pseudoreplication).

## Mapping from K-Dense artifacts

| Source | Maps to |
|--------|---------|
| schedule row count / power n | `planned_units` |
| randomization seed | `design_seed` |
| schedule.csv bytes | `schedule_hash` |
| confirmatory analysis decision rules | `confirmatory_analyses` |
| HARKing freeze before outcomes | `frozen_before_outcomes: true` |
