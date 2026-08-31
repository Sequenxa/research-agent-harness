---
name: research-harness
description: Install and operate the research reconciliation harness — init contract, validate, promote desired fingerprint, run supervisor loop, reconcile, status, preflight. Use when dropping the harness into a research project, wiring research-harness CLI, or supervising workers until they are live and stable.
license: Apache-2.0
compatibility: Requires Python 3.13+, uv, and research-harness installed (uv sync in this repo or pip install).
metadata:
  version: "1.0"
---

# Research Harness

A **reconciliation controller** for research execution — not a generic AI scientist.

Core principle: a code/configuration change is not completion. Completion means the intended system is live, observable, progressing, measured, and stable.

## When to use

- Dropping this harness into another research repo
- Creating or validating `contract.yaml`
- Promoting desired deployment fingerprints
- Running `research-harness run` / `reconcile` / `status` / `preflight`
- Separating operational failure from scientific results

## Install

```bash
# From the harness repo (external drive: put venv on local disk)
export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv
uv sync --dev

# Or install as a dependency in the host project
uv add research-harness  # when published; else path dependency
```

Agent Skills / Plugin install (this repo root is an Agent Plugins package):

```bash
npx skills add <owner>/research-agent-harness
# or
gh skill install <owner>/research-agent-harness research-harness
```

Also install sibling skills when needed:

- `research-harness-adapter` — implement Runtime/Checkpoint/Diagnostics
- `research-harness-plan` — optional experiment/plan.json from methodology artifacts

Domain science skills (Scanpy, DOE, hypothesis-generation, etc.) belong in the **host** project via [K-Dense scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills) — do not vendor them into harness core.

## Drop-in checklist

1. Install this skill (+ adapter skill; optionally plan skill).
2. `uv run research-harness init --id <project> --objective "..."`
3. Optionally write `experiment/plan.json` (see `research-harness-plan`) and point `contract.yaml` at it.
4. Implement adapters (`research-harness-adapter`).
5. Register runtime entry point or set `runtime_loader` in the contract.
6. `research-harness promote --from repository`
7. `research-harness run` (or `reconcile` / `status`)

## Commands

```bash
uv run research-harness init --id my-project --objective "Determine whether X affects Y."
uv run research-harness validate --contract ./contract.yaml
uv run research-harness status
uv run research-harness promote --from repository
uv run research-harness preflight full_relaunch
uv run research-harness run --runtime <plugin-or-module:callable> --max-ticks 5
uv run research-harness reconcile
uv run research-harness stop
uv run research-harness runtimes list
```

## Contract essentials

See `references/contract_overview.md`. Key sections: `fingerprint`, `progress`, `validity`, `authority`, `verification`, optional `experiment` and `runtime_loader`.

## Boundaries

**Always do:** validate contracts; treat fingerprint mismatch as operational; freeze experiment plans before outcomes when using `experiment:`.

**Ask first:** changing authority bounds; architecture-level relaunch policies.

**Never do:** put provider SDKs or hypothesis scoring in harness core; auto-accept/reject scientific hypotheses; collapse operational failure into a scientific result.

## Related

- Full adapter guide: repo `docs/ADAPTERS.md`
- Spec: `docs/SPEC.md`
- Adapter skill: `research-harness-adapter`
- Plan skill: `research-harness-plan`
