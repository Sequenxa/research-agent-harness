# Research Agent Harness — Agent Instructions

## Philosophy

This is a **reconciliation controller** for research execution — not a generic autonomous researcher.

> A code/configuration change is not completion. Completion means the intended system is live, observable, progressing, measured, and stable.

When implementing:
- Prefer simple composable modules; no abstractions without a current use case
- Verify behavior end-to-end; unit tests alone are not completion
- See [docs/SPEC.md](docs/SPEC.md) for the full 25-section specification
- Implement phase-by-phase (see Implementation Slices in SPEC.md); do not scaffold everything at once

## Development rules

- Python 3.13, uv, pytest, ruff, mypy, Pydantic, Typer, SQLite (stdlib)
- On external volumes, put the venv on local disk: `export UV_PROJECT_ENVIRONMENT=/tmp/research-harness-venv` before `uv sync` (macOS `._*` sidecars can break `.venv` on the drive)
- v0.1 adapters: `RuntimeAdapter`, `CheckpointAdapter`, `DiagnosticsAdapter` only
- No web UI, K8s, Redis, or provider SDK in core for v0.1
- Do not integrate into existing Sequenxa repos until the deterministic harness proves itself

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
