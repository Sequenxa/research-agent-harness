from __future__ import annotations

from dataclasses import dataclass

from research_harness.adapters.base import RuntimeAdapter
from research_harness.models.enums import MutationReadinessStatus
from research_harness.models.mutation import MutationReadiness, MutationRepairResult


def mutation_preflight_for(runtime: RuntimeAdapter, action: str) -> MutationReadiness:
    """Ask the project adapter whether a contemplated mutation is safe."""
    return runtime.mutation_preflight(action)


def repair_mutation_prerequisite_for(
    runtime: RuntimeAdapter,
    repair_id: str,
) -> MutationRepairResult:
    """Apply and verify one permitted prerequisite repair."""
    return runtime.repair_mutation_prerequisite(repair_id)


@dataclass(frozen=True)
class PreflightRemediationResult:
    final: MutationReadiness
    repairs_applied: list[str]
    blocked_reason: str | None = None


def remediate_preflight(
    runtime: RuntimeAdapter,
    action: str,
    *,
    max_rounds: int = 3,
    observe_only: bool = False,
) -> PreflightRemediationResult:
    """Run preflight, applying permitted repairs until READY or blocked."""
    repairs_applied: list[str] = []
    for _ in range(max_rounds):
        preflight = mutation_preflight_for(runtime, action)
        if preflight.status == MutationReadinessStatus.READY:
            return PreflightRemediationResult(final=preflight, repairs_applied=repairs_applied)
        if preflight.status == MutationReadinessStatus.BLOCKED:
            return PreflightRemediationResult(
                final=preflight,
                repairs_applied=repairs_applied,
                blocked_reason=preflight.reason,
            )
        if preflight.status != MutationReadinessStatus.REPAIRABLE:
            return PreflightRemediationResult(
                final=preflight,
                repairs_applied=repairs_applied,
                blocked_reason=preflight.reason or f"unknown preflight status: {preflight.status}",
            )
        if not preflight.repairs:
            reason = preflight.reason or "REPAIRABLE but no repairs specified"
            return PreflightRemediationResult(
                final=MutationReadiness.blocked(action, reason=reason, checks=preflight.checks),
                repairs_applied=repairs_applied,
                blocked_reason=reason,
            )
        if observe_only:
            return PreflightRemediationResult(
                final=preflight,
                repairs_applied=[repair.repair_id for repair in preflight.repairs],
                blocked_reason=None,
            )
        for repair in preflight.repairs:
            result = repair_mutation_prerequisite_for(runtime, repair.repair_id)
            repairs_applied.append(repair.repair_id)
            if not result.succeeded or not result.verified:
                reason = result.detail or f"repair {repair.repair_id} failed verification"
                return PreflightRemediationResult(
                    final=MutationReadiness.blocked(action, reason=reason),
                    repairs_applied=repairs_applied,
                    blocked_reason=reason,
                )
    reason = "preflight remediation exceeded max rounds"
    return PreflightRemediationResult(
        final=MutationReadiness.blocked(action, reason=reason),
        repairs_applied=repairs_applied,
        blocked_reason=reason,
    )
