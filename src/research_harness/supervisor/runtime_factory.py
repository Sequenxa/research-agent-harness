from __future__ import annotations

from pathlib import Path
from typing import Literal

from research_harness.adapters.base import RuntimeAdapter
from research_harness.adapters.failing_worker import FailingWorkerRuntime
from research_harness.adapters.file_runtime import FileRuntimeAdapter
from research_harness.runtime.io import load_fingerprint_file
from research_harness.watchdog import ProgressContext

RuntimeKind = Literal["file", "failing-worker"]


def create_runtime(
    *,
    kind: RuntimeKind,
    project_id: str,
    state_dir: Path,
    pending_desired: dict[str, str] | None = None,
) -> RuntimeAdapter:
    if kind == "failing-worker":
        return FailingWorkerRuntime(project_id=project_id, state_dir=state_dir)
    return FileRuntimeAdapter(
        project_id=project_id,
        state_dir=state_dir,
        pending_desired=pending_desired,
    )


def as_checkpoint(runtime: RuntimeAdapter):
    from research_harness.adapters.base import CheckpointAdapter

    return runtime if isinstance(runtime, CheckpointAdapter) else None


def as_diagnostics(runtime: RuntimeAdapter):
    from research_harness.adapters.base import DiagnosticsAdapter

    return runtime if isinstance(runtime, DiagnosticsAdapter) else None


def progress_context_for(runtime: RuntimeAdapter) -> ProgressContext:
    getter = getattr(runtime, "progress_context", None)
    if callable(getter):
        return getter()
    return ProgressContext()


def custom_validity_for(runtime: RuntimeAdapter) -> dict[str, bool]:
    getter = getattr(runtime, "custom_validity_results", None)
    if callable(getter):
        results = getter()
        return dict(results)
    return {}


def desired_fingerprint_for(
    runtime: RuntimeAdapter,
    *,
    state_dir: Path,
    runtime_kind: RuntimeKind,
) -> dict[str, str]:
    """Resolve the desired deployment fingerprint.

    Explicit ``desired_fingerprint.json`` wins. Otherwise desired defaults to
    the **running** fingerprint so repository edits do not become deployment
    instructions until promoted.
    """
    desired_path = state_dir / "desired_fingerprint.json"
    if desired_path.exists():
        return load_fingerprint_file(desired_path)
    if runtime_kind == "failing-worker" and isinstance(runtime, FailingWorkerRuntime):
        state = runtime.store.load_state()
        running = dict(runtime.inspect().fingerprint)
        if state.pending_config_hash is not None:
            pending = dict(running)
            pending["config_hash"] = state.pending_config_hash
            return pending
        return running
    pending = getattr(runtime, "pending_fingerprint", None)
    if pending:
        return dict(pending)
    return dict(runtime.inspect().fingerprint)
