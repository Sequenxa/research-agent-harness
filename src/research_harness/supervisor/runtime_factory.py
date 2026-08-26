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
    if runtime_kind == "failing-worker" and isinstance(runtime, FailingWorkerRuntime):
        config = runtime.store.load_config()
        state = runtime.store.load_state()
        desired = config.fingerprint()
        if state.pending_config_hash is not None:
            desired["config_hash"] = state.pending_config_hash
        return desired
    pending = getattr(runtime, "pending_fingerprint", None)
    if pending:
        return dict(pending)
    desired_path = state_dir / "desired_fingerprint.json"
    if desired_path.exists():
        return load_fingerprint_file(desired_path)
    return dict(runtime.inspect().fingerprint)
