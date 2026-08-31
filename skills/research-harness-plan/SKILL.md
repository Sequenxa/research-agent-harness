---
name: research-harness-plan
description: Project methodology artifacts into experiment/plan.json and optional schedule.csv for the research harness. Use when mapping experimental-design, statistical-power, or hypothesis-generation outputs into a frozen harness plan, or when wiring contract.experiment pointers before research-harness run.
license: Apache-2.0
compatibility: Requires Python 3.13+ standard library. Optional K-Dense experimental-design / statistical-power / hypothesis-generation skills in the host project.
metadata:
  version: "1.0"
---

# Research Harness Plan

The harness reconciles against **planned work**, not invented science. This skill projects methodology outputs into a thin `experiment/plan.json` the controller can read.

## When to use

- After designing a study (DOE / randomization / power / preregistration)
- Before workers produce outcomes (freeze the plan)
- When `contract.yaml` should point at `experiment:` artifacts

## Non-goals

- Do **not** reimplement experimental-design or hypothesis-generation here.
- Do **not** auto-accept or auto-reject hypotheses from `decision_rule` text.
- Do **not** vendor Scanpy/RDKit/ICH validation into harness core.

## Workflow

1. **Produce upstream artifacts** (host project), if available:
   - K-Dense `experimental-design` → seeded `schedule.csv`
   - K-Dense `statistical-power` → planned n
   - K-Dense `hypothesis-generation` → hypothesis record with confirmatory `decision_rule`s
2. **Project** into harness-owned `experiment/plan.json` (schema: `references/plan_schema.md`).
3. **Point the contract**:

```yaml
experiment:
  plan: experiment/plan.json
  schedule: experiment/schedule.csv
validity:
  expected_units: 4   # should match planned_units; harness prefers plan when loaded
fingerprint:
  fields: [git_sha, config_hash, plan_hash, design_seed]
```

4. **Freeze** — set `frozen_before_outcomes: true`, recompute `plan_hash`, commit the plan before outcomes are visible to analysis.
5. **Run** — `research-harness validate` then `research-harness run`.

## Emit helper

```bash
python3 scripts/emit_plan.py \
  --planned-units 4 \
  --design-seed 42 \
  --unit-of-analysis "independent run cell" \
  --schedule experiment/schedule.csv \
  --decision-rule "Interpret interval estimate; do not auto-select a hypothesis." \
  --freeze \
  -o experiment/plan.json
```

## How the harness uses the plan

| Field | Effect |
|-------|--------|
| `planned_units` | Completion / status denominator |
| `plan_hash` | Integrity + optional fingerprint `research_semantic` |
| `schedule_hash` | Validity fails if live schedule drifts |
| `frozen_before_outcomes` | Block validity after progress if still false |
| `confirmatory_analyses[].decision_rule` | Recorded text only — never auto-scored |

Absent `experiment:` → existing v1.1 behavior unchanged.

## Related

- Operate: `research-harness`
- Adapters: `research-harness-adapter`
- Upstream (optional): K-Dense `experimental-design`, `statistical-power`, `hypothesis-generation`
