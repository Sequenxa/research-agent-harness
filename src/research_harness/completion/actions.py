from __future__ import annotations

from pathlib import Path

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.models import ProjectContract
from research_harness.ledger import LedgerStore

COMPLETION_MARKER = "completion_actions.done"


def execute_completion_actions(
    *,
    contract: ProjectContract,
    runtime: RuntimeAdapter,
    ledger: LedgerStore,
    state_dir: Path,
    observe_only: bool = False,
) -> list[str]:
    """Run contract completion actions idempotently."""
    marker_path = Path(state_dir) / COMPLETION_MARKER
    if marker_path.exists():
        return []

    actions_taken: list[str] = []
    for action in contract.completion.on_complete:
        if action == "snapshot_ledger":
            events = ledger.list_events(project_id=contract.project.id)
            actions_taken.append(f"snapshot_ledger:{len(events)}")
        elif action == "stop_workers":
            if observe_only:
                actions_taken.append("would_stop_workers")
            elif _stop_workers(runtime):
                actions_taken.append("stop_workers")
            else:
                actions_taken.append("stop_workers_unavailable")
        else:
            actions_taken.append(f"unsupported:{action}")

    if not observe_only:
        marker_path.write_text("done\n", encoding="utf-8")
    return actions_taken


def _stop_workers(runtime: RuntimeAdapter) -> bool:
    return runtime.stop_verified()
