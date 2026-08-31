from __future__ import annotations

from dataclasses import dataclass

from research_harness.contract.models import ProjectContract
from research_harness.experiment.plan import (
    ExperimentPlan,
    compute_plan_hash,
    resolve_expected_units,
)
from research_harness.models.state import ObservedState
from research_harness.validity.evaluator import ValidityResult


@dataclass(frozen=True)
class CompletionResult:
    met: bool
    reason: str


def evaluate_completion(
    *,
    contract: ProjectContract,
    observed: ObservedState,
    validity: ValidityResult,
    plan: ExperimentPlan | None = None,
) -> CompletionResult:
    """Evaluate the contract completion condition (v0.1 supported forms).

    When ``plan`` is present, units threshold comes from ``planned_units``,
    and completion additionally requires a frozen, hash-valid plan.
    """
    condition = contract.completion.condition.strip()
    expected_units = resolve_expected_units(contract=contract, plan=plan)
    units_ok = observed.completed_units >= expected_units
    validity_ok = validity.passed

    if plan is not None:
        if compute_plan_hash(plan) != plan.plan_hash:
            return CompletionResult(met=False, reason="plan_hash invalid")
        if not plan.frozen_before_outcomes:
            return CompletionResult(met=False, reason="experiment plan not frozen")
        # Fingerprint may carry plan_hash as research_semantic; if present, require match.
        observed_plan_hash = observed.fingerprint.get("plan_hash")
        if observed_plan_hash is not None and observed_plan_hash != plan.plan_hash:
            return CompletionResult(
                met=False,
                reason=(
                    f"observed plan_hash {observed_plan_hash} != frozen {plan.plan_hash}"
                ),
            )

    if (
        condition == "units_completed >= 1000 and validity.passed"
        or ("units_completed >=" in condition and "validity.passed" in condition)
    ):
        met = units_ok and validity_ok
    elif condition.startswith("units_completed >="):
        threshold = _parse_units_threshold(condition)
        met = units_ok if plan is not None else observed.completed_units >= threshold
    else:
        return CompletionResult(
            met=False,
            reason=f"unsupported completion condition: {condition}",
        )

    if met:
        return CompletionResult(met=True, reason="completion condition satisfied")
    return CompletionResult(
        met=False,
        reason=f"units={observed.completed_units}/{expected_units}, validity={validity_ok}",
    )


def _parse_units_threshold(condition: str) -> int:
    # units_completed >= 1000
    parts = condition.replace("units_completed >=", "").strip()
    return int(parts.split()[0])
