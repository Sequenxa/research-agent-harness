from __future__ import annotations

from pathlib import Path

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.models import ProjectContract
from research_harness.models.enums import (
    Health,
    InspectionStatus,
    MutationReadinessStatus,
    Progress,
    RuntimeFreshness,
)
from research_harness.models.mutation import MutationReadiness
from research_harness.models.state import ObservedState
from research_harness.runtime.fingerprint_state import FingerprintState
from research_harness.runtime.io import load_fingerprint_file
from research_harness.runtime.mutation import mutation_preflight_for
from research_harness.supervisor.runtime_factory import desired_fingerprint_for

from research_harness.supervisor.loop import RuntimeKind


def repository_fingerprint_for(
    runtime: RuntimeAdapter,
    *,
    state_dir: Path,
) -> dict[str, str] | None:
    getter = getattr(runtime, "repository_fingerprint", None)
    if callable(getter):
        result = getter()
        return dict(result) if result is not None else None
    repo_path = state_dir / "repository_fingerprint.json"
    if repo_path.exists():
        return load_fingerprint_file(repo_path)
    return None


def resolve_fingerprint_state(
    *,
    runtime: RuntimeAdapter,
    contract: ProjectContract,
    state_dir: Path,
    runtime_kind: RuntimeKind,
    observed: ObservedState | None = None,
) -> FingerprintState:
    current = observed or runtime.inspect()
    return FingerprintState(
        running=dict(current.fingerprint),
        desired=desired_fingerprint_for(
            runtime,
            state_dir=state_dir,
            runtime_kind=runtime_kind,  # type: ignore[arg-type]
        ),
        repository=repository_fingerprint_for(runtime, state_dir=state_dir),
    )


def assess_operation(
    *,
    runtime: RuntimeAdapter,
    contract: ProjectContract,
    state_dir: Path,
    runtime_kind: RuntimeKind,
    observed: ObservedState | None = None,
    mutation_action: str | None = None,
) -> OperationalAssessment:
    current = observed or runtime.inspect()
    fingerprints = resolve_fingerprint_state(
        runtime=runtime,
        contract=contract,
        state_dir=state_dir,
        runtime_kind=runtime_kind,
        observed=current,
    )
    freshness = fingerprints.runtime_freshness(fields=contract.fingerprint.fields)
    mutation: MutationReadiness | None = None
    if mutation_action is not None:
        mutation = mutation_preflight_for(runtime, mutation_action)
    return OperationalAssessment(
        runtime_health=current.health,
        progress=current.progress,
        runtime_freshness=freshness,
        inspection=current.inspection,
        fingerprints=fingerprints,
        repository_ahead=fingerprints.repository_ahead_of_desired(
            fields=contract.fingerprint.fields
        ),
        reconciliation_required=fingerprints.reconciliation_required(
            fields=contract.fingerprint.fields
        ),
        mutation=mutation,
    )


class OperationalAssessment:
    """Orthogonal operational signals from a single inspect pass."""

    __slots__ = (
        "runtime_health",
        "progress",
        "runtime_freshness",
        "inspection",
        "fingerprints",
        "repository_ahead",
        "reconciliation_required",
        "mutation",
    )

    def __init__(
        self,
        *,
        runtime_health: Health,
        progress: Progress,
        runtime_freshness: RuntimeFreshness,
        inspection: InspectionStatus,
        fingerprints: FingerprintState,
        repository_ahead: bool,
        reconciliation_required: bool,
        mutation: MutationReadiness | None = None,
    ) -> None:
        self.runtime_health = runtime_health
        self.progress = progress
        self.runtime_freshness = runtime_freshness
        self.inspection = inspection
        self.fingerprints = fingerprints
        self.repository_ahead = repository_ahead
        self.reconciliation_required = reconciliation_required
        self.mutation = mutation

    @property
    def mutation_readiness(self) -> MutationReadinessStatus | None:
        if self.mutation is None:
            return None
        return self.mutation.status

    @property
    def mutation_reason(self) -> str | None:
        if self.mutation is None:
            return None
        return self.mutation.reason
