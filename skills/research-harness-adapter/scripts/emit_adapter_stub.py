#!/usr/bin/env python3
"""Emit a minimal RuntimeAdapter factory stub for a host project."""

from __future__ import annotations

import argparse
from pathlib import Path

TEMPLATE = '''\
"""Harness runtime adapter for {project_label}."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_harness.adapters.base import (
    CheckpointAdapter,
    DiagnosticsAdapter,
    RuntimeAdapter,
)
from research_harness.models.enums import Health, Lifecycle, Progress, RuntimeFreshness
from research_harness.models.mutation import MutationReadiness
from research_harness.models.state import ObservedState


class {class_name}(RuntimeAdapter, CheckpointAdapter, DiagnosticsAdapter):
    """Thin project-specific runtime control surface."""

    def __init__(self, *, project_id: str, state_dir: Path) -> None:
        self.project_id = project_id
        self.state_dir = Path(state_dir)

    def inspect(self) -> ObservedState:
        # TODO: read applied runtime state (not pending file edits).
        return ObservedState(
            project_id=self.project_id,
            observed_at=datetime.now(UTC),
            lifecycle=Lifecycle.STOPPED,
            health=Health.HEALTHY,
            progress=Progress.STALLED,
            runtime_freshness=RuntimeFreshness.STALE,
            fingerprint={{}},
            completed_units=0,
        )

    def restart_worker(self) -> None:
        raise NotImplementedError("restart_worker")

    def relaunch(self, action: str) -> None:
        raise NotImplementedError(f"relaunch({{action!r}})")

    def stop(self) -> None:
        raise NotImplementedError("stop")

    def mutation_preflight(self, action: str) -> MutationReadiness:
        return MutationReadiness.ready(action)

    def fingerprint_field_classifications(self) -> dict[str, str]:
        return {{
            "git_sha": "deployment",
            "config_hash": "deployment",
            "plan_hash": "research_semantic",
            "design_seed": "research_semantic",
        }}

    def latest_checkpoint(self) -> dict[str, Any] | None:
        return None

    def save_checkpoint(self, payload: dict[str, Any]) -> str:
        path = self.state_dir / "checkpoint.json"
        path.write_text(str(payload), encoding="utf-8")
        return str(path)

    def collect(self, *, symptom: str) -> dict[str, Any]:
        return {{"symptom": symptom}}


def create_runtime(*, project_id: str, state_dir: Path, **options: Any) -> {class_name}:
    """Entry-point factory for research_harness.runtimes."""
    return {class_name}(project_id=project_id, state_dir=state_dir)
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default="my_pkg", help="Python package name (label only)")
    parser.add_argument("--class-name", default="MyProjectRuntime")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    text = TEMPLATE.format(
        project_label=args.package,
        class_name=args.class_name,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
