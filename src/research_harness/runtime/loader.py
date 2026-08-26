from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from research_harness.adapters.base import RuntimeAdapter

ENTRY_POINT_GROUP = "research_harness.runtimes"
BUILTIN_RUNTIMES = frozenset({"file", "failing-worker"})

RuntimeFactory = Callable[..., RuntimeAdapter]


@dataclass(frozen=True)
class RegisteredRuntime:
    """One runtime factory registered via entry points."""

    name: str
    entrypoint: str


@dataclass
class RuntimeLoadRequest:
    """Resolved instructions for loading a project runtime."""

    label: str
    entrypoint: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def list_registered_runtimes() -> list[RegisteredRuntime]:
    """Discover project runtime factories registered as entry points."""
    discovered = entry_points(group=ENTRY_POINT_GROUP)
    runtimes: list[RegisteredRuntime] = []
    for item in discovered:
        runtimes.append(RegisteredRuntime(name=item.name, entrypoint=item.value))
    return sorted(runtimes, key=lambda runtime: runtime.name)


def registered_runtime_names() -> dict[str, str]:
    """Map registered runtime name to its entry-point target."""
    return {runtime.name: runtime.entrypoint for runtime in list_registered_runtimes()}


def parse_entrypoint_target(target: str) -> tuple[str, str]:
    """Parse ``module:callable`` into importable module and attribute names."""
    if ":" not in target:
        msg = f"Entry point must use module:callable format, got {target!r}"
        raise ValueError(msg)
    module_name, attribute = target.split(":", 1)
    module_name = module_name.strip()
    attribute = attribute.strip()
    if not module_name or not attribute:
        msg = f"Invalid entry point target: {target!r}"
        raise ValueError(msg)
    return module_name, attribute


def import_runtime_factory(target: str) -> RuntimeFactory:
    """Import a runtime factory from ``module:callable``."""
    module_name, attribute = parse_entrypoint_target(target)
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if factory is None or not callable(factory):
        msg = f"Runtime factory not callable: {target}"
        raise TypeError(msg)
    return factory  # type: ignore[no-any-return]


def invoke_runtime_factory(
    factory: RuntimeFactory,
    *,
    project_id: str,
    state_dir: Path,
    options: dict[str, Any] | None = None,
) -> RuntimeAdapter:
    """Call a project runtime factory with the harness-standard kwargs."""
    kwargs = dict(options or {})
    try:
        runtime = factory(project_id=project_id, state_dir=state_dir, **kwargs)
    except TypeError:
        runtime = factory(project_id, state_dir)
    if not isinstance(runtime, RuntimeAdapter):
        msg = f"Runtime factory returned {type(runtime)!r}, expected RuntimeAdapter"
        raise TypeError(msg)
    return runtime


def load_runtime_from_entrypoint(
    target: str,
    *,
    project_id: str,
    state_dir: Path,
    options: dict[str, Any] | None = None,
) -> RuntimeAdapter:
    """Load a runtime adapter from an explicit or registered entry point."""
    factory = import_runtime_factory(target)
    return invoke_runtime_factory(
        factory,
        project_id=project_id,
        state_dir=state_dir,
        options=options,
    )


def resolve_entrypoint_target(*, label: str) -> str:
    """Resolve a registered runtime name or pass through ``module:callable``."""
    if ":" in label:
        return label
    registered = registered_runtime_names()
    if label in registered:
        return registered[label]
    msg = (
        f"Unknown runtime {label!r}. "
        f"Use module:callable or one of: {', '.join(sorted(registered)) or '(none registered)'}"
    )
    raise ValueError(msg)


def resolve_runtime_load_request(
    *,
    runtime: str | None,
    entrypoint: str | None,
    contract_runtime_loader: object | None,
    state_dir: Path,
) -> RuntimeLoadRequest:
    """Resolve runtime loading instructions from CLI flags and contract."""
    options: dict[str, Any] = {}
    loader = contract_runtime_loader
    if entrypoint:
        return RuntimeLoadRequest(label="entrypoint", entrypoint=entrypoint, options=options)
    if loader is not None:
        loader_entrypoint = getattr(loader, "entrypoint", None)
        loader_plugin = getattr(loader, "plugin", None)
        loader_options = getattr(loader, "options", None)
        if isinstance(loader_options, dict):
            options = dict(loader_options)
        if loader_entrypoint:
            return RuntimeLoadRequest(
                label="entrypoint",
                entrypoint=str(loader_entrypoint),
                options=options,
            )
        if loader_plugin:
            return RuntimeLoadRequest(label=str(loader_plugin), options=options)
    if runtime:
        if ":" in runtime:
            return RuntimeLoadRequest(label="entrypoint", entrypoint=runtime, options=options)
        if runtime in BUILTIN_RUNTIMES or runtime in registered_runtime_names():
            return RuntimeLoadRequest(label=runtime, options=options)
        return RuntimeLoadRequest(label=runtime, options=options)
    if (state_dir / "worker_state.json").exists():
        return RuntimeLoadRequest(label="failing-worker", options=options)
    return RuntimeLoadRequest(label="file", options=options)
