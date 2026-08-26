from __future__ import annotations

from research_harness.adapters.base import RuntimeAdapter
from research_harness.models.mutation import MutationReadiness


def mutation_preflight_for(runtime: RuntimeAdapter, action: str) -> MutationReadiness:
    """Ask the project adapter whether a contemplated mutation is safe."""
    preflight = getattr(runtime, "mutation_preflight", None)
    if callable(preflight):
        return preflight(action)
    return MutationReadiness.ready(action)
